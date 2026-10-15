// «Nodos vecinos» (malla): Bonjour/mDNS entre los nodos del MISMO evento en la misma red.
//
// La app sólo descubre: anuncia este nodo como `_nodocamporee._tcp` (TXT: evento, nodo, nombre,
// versión del protocolo) y busca a los demás. Los vecinos encontrados se escriben en
// run/malla-vecinos.json; el servicio «malla» del nodo (Python, scripts/nodo_malla.py) los lee, se
// autentica con cada uno (llaves Ed25519 del nodo, aval de la nube o emparejamiento) e intercambia
// los avances firmados. Nada sale a internet; ningún puerto se abre hacia internet.
//
// Corre como LaunchAgent mientras el nodo está encendido: `Nodo Camporee --malla --run <dir>`.
// Opciones de prueba: --puerto N (el que se anuncia; por defecto 80), --nombre-servicio S,
// --local (los vecinos que resultan ser ESTA Mac se escriben como 127.0.0.1).
import Darwin
import dnssd
import Foundation

enum MallaBonjour {
    static let tipo = "_nodocamporee._tcp."

    struct Opciones {
        var run = ""
        var puerto: Int32 = 80
        var nombreServicio: String? = nil
        var local = false
    }

    static func opciones(_ args: [String]) -> Opciones {
        var o = Opciones()
        var i = 0
        while i < args.count {
            let v = i + 1 < args.count ? args[i + 1] : ""
            switch args[i] {
            case "--run": o.run = v; i += 1
            case "--puerto": o.puerto = Int32(v) ?? 80; i += 1
            case "--nombre-servicio": o.nombreServicio = v; i += 1
            case "--local": o.local = true
            default: break
            }
            i += 1
        }
        return o
    }

    static func log(_ s: String) {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        print("\(f.string(from: Date())) \(s)"); fflush(stdout)
    }

    /// Identidad de este nodo para el anuncio (de run/nodo-info.json, que escribe el motor).
    struct Yo: Equatable { var evento = ""; var nodo = ""; var nombre = "" }

    static func leerYo(_ run: String) -> Yo {
        var y = Yo()
        guard let d = FileManager.default.contents(atPath: run + "/nodo-info.json"),
              var o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return y }
        if let n = o["node"] as? [String: Any] { o = n }
        y.evento = (o["event_id"] as? String) ?? ""
        y.nodo = (o["id"] as? String) ?? (o["node_id"] as? String) ?? ""
        y.nombre = (o["label"] as? String) ?? ""
        return y
    }

    /// TXT de Bonjour: claves cortas (cada valor < 255 bytes).
    static func txt(_ y: Yo) -> [String: Data] {
        ["v": Data("1".utf8), "ev": Data(y.evento.utf8), "n": Data(y.nodo.utf8), "l": Data(String(y.nombre.prefix(60)).utf8)]
    }

    static func leerTXT(_ d: Data?) -> [String: String] {
        guard let d else { return [:] }
        var out: [String: String] = [:]
        for (k, v) in NetService.dictionary(fromTXTRecord: d) { out[k] = String(data: v, encoding: .utf8) ?? "" }
        return out
    }

    /// Direcciones IPv4 de las interfaces de esta Mac (para reconocer «soy yo»).
    static func misIPs() -> Set<String> {
        var out = Set<String>()
        var ifa: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifa) == 0, let first = ifa else { return out }
        defer { freeifaddrs(ifa) }
        var p: UnsafeMutablePointer<ifaddrs>? = first
        while let x = p {
            if let sa = x.pointee.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET) {
                var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                if getnameinfo(sa, socklen_t(sa.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 {
                    out.insert(String(cString: host))
                }
            }
            p = x.pointee.ifa_next
        }
        return out
    }

    static func ipv4(de direcciones: [Data]) -> [String] {
        var out: [String] = []
        for d in direcciones {
            d.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                guard let sa = raw.baseAddress?.assumingMemoryBound(to: sockaddr.self), sa.pointee.sa_family == UInt8(AF_INET) else { return }
                var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                if getnameinfo(sa, socklen_t(d.count), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 {
                    let ip = String(cString: host)
                    if !ip.hasPrefix("169.254.") { out.append(ip) }
                }
            }
        }
        return out
    }

    /// Vecino visto por Bonjour (lo que se escribe para el servicio «malla»).
    struct Vecino: Codable, Equatable {
        var url: String
        var nombre: String
        var node_id: String
        var visto: Double
    }

    /// Bonjour con la API dns_sd de Apple (la misma de `dns-sd`): funciona igual en un proceso sin
    /// ventana (LaunchAgent). Todo corre en una cola serie.
    final class Agente {
        let o: Opciones
        let cola = DispatchQueue(label: "malla.bonjour")
        var yo = Yo()
        var registro: DNSServiceRef?
        var busqueda: DNSServiceRef?
        var pendientes: [DNSServiceRef] = []
        var vistos: [String: Vecino] = [:]          // por id del nodo
        var instancias: [String: String] = [:]      // nombre de instancia Bonjour → id del nodo
        var timer: DispatchSourceTimer?

        init(_ o: Opciones) { self.o = o }
        var ctx: UnsafeMutableRawPointer { Unmanaged.passUnretained(self).toOpaque() }
        static func de(_ p: UnsafeMutableRawPointer?) -> Agente { Unmanaged<Agente>.fromOpaque(p!).takeUnretainedValue() }

        func arrancar() {
            cola.async { [self] in
                buscar()
                revisarIdentidad()
                escribir()
                let t = DispatchSource.makeTimerSource(queue: cola)
                t.schedule(deadline: .now() + 15, repeating: 15)
                t.setEventHandler { [weak self] in
                    guard let self else { return }
                    self.revisarIdentidad()
                    // Vecinos que dejaron de anunciarse hace más de 2 min se olvidan.
                    let limite = Date().timeIntervalSince1970 - 120
                    self.vistos = self.vistos.filter { $0.value.visto >= limite }
                    self.escribir()
                }
                t.resume()
                timer = t
            }
        }

        func buscar() {
            var ref: DNSServiceRef?
            let err = DNSServiceBrowse(&ref, 0, 0, MallaBonjour.tipo, "local.", { _, flags, iface, err, nombre, tipo, dominio, ctx in
                let a = Agente.de(ctx)
                guard err == kDNSServiceErr_NoError, let nombre, let tipo, let dominio else {
                    MallaBonjour.log("no pude buscar vecinos (error \(err); ¿permiso de Red local para Nodo Camporee?)"); return
                }
                let n = String(cString: nombre)
                if flags & DNSServiceFlags(kDNSServiceFlagsAdd) == 0 {
                    if let nodo = a.instancias.removeValue(forKey: n), a.vistos.removeValue(forKey: nodo) != nil {
                        MallaBonjour.log("se fue: \(n)"); a.escribir()
                    }
                    return
                }
                a.resolver(n, tipo: String(cString: tipo), dominio: String(cString: dominio), iface: iface)
            }, ctx)
            if err == kDNSServiceErr_NoError, let ref { DNSServiceSetDispatchQueue(ref, cola); busqueda = ref }
            else { MallaBonjour.log("no pude buscar vecinos (error \(err))") }
        }

        func resolver(_ nombre: String, tipo: String, dominio: String, iface: UInt32) {
            if registro != nil && nombreAnunciado == nombre { return }   // soy yo
            var ref: DNSServiceRef?
            let err = DNSServiceResolve(&ref, 0, iface, nombre, tipo, dominio, { ref, _, iface, err, _, host, puertoBE, txtLen, txt, ctx in
                let a = Agente.de(ctx)
                defer { a.soltar(ref) }
                guard err == kDNSServiceErr_NoError, let host else { return }
                let t = MallaBonjour.leerTXT(txt.map { Data(bytes: $0, count: Int(txtLen)) })
                let puerto = UInt16(bigEndian: puertoBE)
                a.direccion(host: String(cString: host), puerto: puerto, txt: t, iface: iface, instancia: a.resolviendo[ref.map { "\($0)" } ?? ""] ?? "")
            }, ctx)
            if err == kDNSServiceErr_NoError, let ref { DNSServiceSetDispatchQueue(ref, cola); pendientes.append(ref); resolviendo["\(ref)"] = nombre }
        }

        var resolviendo: [String: String] = [:]     // ref (texto) → nombre de instancia

        func direccion(host: String, puerto: UInt16, txt: [String: String], iface: UInt32, instancia: String) {
            guard txt["v"] == "1", let ev = txt["ev"], !ev.isEmpty else { return }
            // Sólo nodos del MISMO evento (la autenticación la hace el servicio «malla»).
            guard ev == yo.evento, let n = txt["n"], !n.isEmpty, n != yo.nodo else { return }
            if !instancia.isEmpty { instancias[instancia] = n }
            let nombre = txt["l"] ?? host
            var ref: DNSServiceRef?
            let caja = Caja(agente: self, nombre: nombre, nodo: n, puerto: puerto)
            let err = DNSServiceGetAddrInfo(&ref, 0, iface, DNSServiceProtocol(kDNSServiceProtocol_IPv4), host, { ref, flags, _, err, _, sa, _, ctx in
                let c = Unmanaged<Caja>.fromOpaque(ctx!).takeUnretainedValue()
                guard err == kDNSServiceErr_NoError, let sa, sa.pointee.sa_family == UInt8(AF_INET) else { return }
                let ip = MallaBonjour.ipv4(de: [Data(bytes: sa, count: Int(sa.pointee.sa_len))]).first
                if let ip { c.agente.encontrado(ip: ip, caja: c) }
                if flags & DNSServiceFlags(kDNSServiceFlagsMoreComing) == 0 { c.agente.soltar(ref); c.liberar() }
            }, Unmanaged.passRetained(caja).toOpaque())
            if err == kDNSServiceErr_NoError, let ref { DNSServiceSetDispatchQueue(ref, cola); pendientes.append(ref) }
        }

        final class Caja {
            let agente: Agente; let nombre: String; let nodo: String; let puerto: UInt16
            var liberada = false
            init(agente: Agente, nombre: String, nodo: String, puerto: UInt16) { self.agente = agente; self.nombre = nombre; self.nodo = nodo; self.puerto = puerto }
            func liberar() { if !liberada { liberada = true; Unmanaged.passUnretained(self).release() } }
        }

        func encontrado(ip ipLeida: String, caja c: Caja) {
            var ip = ipLeida
            if o.local, MallaBonjour.misIPs().contains(ip) { ip = "127.0.0.1" }
            let url = c.puerto == 80 ? "http://\(ip)" : "http://\(ip):\(c.puerto)"
            let v = Vecino(url: url, nombre: c.nombre, node_id: c.nodo, visto: Date().timeIntervalSince1970)
            if vistos[c.nodo]?.url != url { MallaBonjour.log("vecino: «\(v.nombre)» en \(url)") }
            vistos[c.nodo] = v
            escribir()
        }

        func soltar(_ ref: DNSServiceRef?) {
            guard let ref else { return }
            if let i = pendientes.firstIndex(where: { $0 == ref }) { pendientes.remove(at: i); DNSServiceRefDeallocate(ref) }
        }

        var nombreAnunciado = ""

        /// Si cambia el evento o el nodo (se volvió a preparar), se vuelve a anunciar.
        func revisarIdentidad() {
            let nuevo = MallaBonjour.leerYo(o.run)
            if nuevo == yo && registro != nil { return }
            yo = nuevo
            if let r = registro { DNSServiceRefDeallocate(r); registro = nil }
            // Los vecinos son relativos a mi evento: si cambió, se vuelve a empezar.
            vistos = [:]
            if let b = busqueda { DNSServiceRefDeallocate(b); busqueda = nil; buscar() }
            guard !yo.evento.isEmpty, !yo.nodo.isEmpty else {
                MallaBonjour.log("sin evento preparado: no se anuncia (sigo buscando)")
                return
            }
            let nombre = o.nombreServicio ?? "Nodo \(yo.nombre.isEmpty ? String(yo.nodo.prefix(8)) : String(yo.nombre.prefix(40)))"
            let txt = NetService.data(fromTXTRecord: MallaBonjour.txt(yo))
            var ref: DNSServiceRef?
            let err = txt.withUnsafeBytes { (p: UnsafeRawBufferPointer) in
                DNSServiceRegister(&ref, 0, 0, nombre, MallaBonjour.tipo, nil, nil, UInt16(o.puerto).bigEndian,
                                   UInt16(txt.count), p.baseAddress, { _, _, err, nombre, _, _, ctx in
                    let a = Agente.de(ctx)
                    if err == kDNSServiceErr_NoError, let nombre {
                        a.nombreAnunciado = String(cString: nombre)
                        MallaBonjour.log("anunciado en la red: «\(a.nombreAnunciado)»")
                    } else { MallaBonjour.log("no pude anunciar (error \(err))") }
                }, ctx)
            }
            if err == kDNSServiceErr_NoError, let ref { DNSServiceSetDispatchQueue(ref, cola); registro = ref }
            else { MallaBonjour.log("no pude anunciar (error \(err))") }
            MallaBonjour.log("anunciando «\(nombre)» (evento \(yo.evento.prefix(8)), puerto \(o.puerto))")
        }

        func escribir() {
            let lista = vistos.values.sorted { $0.nombre < $1.nombre }
            guard let d = try? JSONEncoder().encode(lista) else { return }
            try? d.write(to: URL(fileURLWithPath: o.run + "/malla-vecinos.json"), options: .atomic)
            // Para la ventana: ¿Bonjour contestó? (Si no, casi siempre falta el permiso de «Red local».)
            let e: [String: Any] = ["anunciado": !nombreAnunciado.isEmpty, "evento": !yo.evento.isEmpty,
                                    "desde": inicio, "t": Date().timeIntervalSince1970, "vistos": lista.count]
            if let j = try? JSONSerialization.data(withJSONObject: e) {
                try? j.write(to: URL(fileURLWithPath: o.run + "/malla-bonjour.json"), options: .atomic)
            }
        }
        let inicio = Date().timeIntervalSince1970
    }

    static func correr(_ args: [String]) -> Int32 {
        let o = opciones(args)
        guard !o.run.isEmpty else { fputs("uso: --malla --run <carpeta run>\n", stderr); return 2 }
        try? FileManager.default.createDirectory(atPath: o.run, withIntermediateDirectories: true)
        let a = Agente(o)
        a.arrancar()
        withExtendedLifetime(a) { dispatchMain() }
    }
}
