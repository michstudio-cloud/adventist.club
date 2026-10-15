// DNS del nodo (sólo para «Mi red»: Google Wifi / Nest Wifi u otro router con DNS personalizado).
//
// El router de la casa/del lugar se configura con «DNS personalizado = esta Mac». Este servidor:
//   • responde events/api/admin.adventist.club (y nodo.local) con la IP de esta Mac en la red
//     (la misma a la que llegó la pregunta), también sin internet;
//   • todo lo demás lo reenvía a DNS públicos (1.1.1.1, 8.8.8.8 — NUNCA al router: con el DNS del
//     router apuntando a esta Mac sería un círculo) con tiempos cortos, y si no hay internet
//     contesta SERVFAIL al instante (los teléfonos no se quedan esperando).
//
// Corre como LaunchAgent del usuario con el ejecutable de la app (`Nodo Camporee --dns`). No hace
// falta root: desde macOS 10.14 un proceso normal puede escuchar en puertos < 1024 si escucha en
// TODAS las direcciones (0.0.0.0), que es lo que se hace aquí. (Colima/Lima NO reenvía UDP con su
// reenviador por SSH, por eso no va en un contenedor; ver README.)
import Darwin
import Foundation

enum ServidorDNS {
    struct Opciones {
        var puerto: UInt16 = 53
        var ipFija: String? = nil            // si no, la IP a la que llegó la pregunta
        var nombres: Set<String> = ["events.adventist.club", "api.adventist.club", "admin.adventist.club", "nodo.local"]
        var reenviar: [String] = ["1.1.1.1", "8.8.8.8"]
        var espera: Double = 1.2             // segundos esperando a los DNS públicos
    }

    static func opciones(_ args: [String]) -> Opciones {
        var o = Opciones()
        var i = 0
        while i < args.count {
            let a = args[i]; let v = i + 1 < args.count ? args[i + 1] : ""
            switch a {
            case "--puerto": o.puerto = UInt16(v) ?? 53; i += 1
            case "--ip": o.ipFija = v.isEmpty ? nil : v; i += 1
            case "--nombres":
                o.nombres = Set(v.split(whereSeparator: { $0 == "," || $0 == " " }).map { $0.lowercased() }); i += 1
            case "--reenviar":
                o.reenviar = v.split(whereSeparator: { $0 == "," || $0 == " " }).map(String.init); i += 1
            case "--espera": o.espera = Double(v) ?? 1.2; i += 1
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

    // MARK: Estado compartido (sin internet => respuesta inmediata)
    final class Salud: @unchecked Sendable {
        private let l = NSLock()
        private var sinInternetHasta = Date.distantPast
        private var ultimoIntento = Date.distantPast
        var propias = 0, reenviadas = 0, fallidas = 0
        /// ¿Se intenta reenviar? Sin internet sólo una pregunta cada 5 s «tantea»; el resto, SERVFAIL ya.
        func intentar() -> Bool {
            l.lock(); defer { l.unlock() }
            let ahora = Date()
            if ahora < sinInternetHasta && ahora.timeIntervalSince(ultimoIntento) < 5 { return false }
            ultimoIntento = ahora
            return true
        }
        func resultado(_ ok: Bool) {
            l.lock(); defer { l.unlock() }
            if ok { sinInternetHasta = .distantPast; reenviadas += 1 }
            else { sinInternetHasta = Date().addingTimeInterval(20); fallidas += 1 }
        }
        func contarPropia() { l.lock(); propias += 1; l.unlock() }
        func resumen() -> String {
            l.lock(); defer { l.unlock() }
            return "propias=\(propias) reenviadas=\(reenviadas) sin_respuesta=\(fallidas) internet=\(Date() < sinInternetHasta ? "no" : "sí")"
        }
    }

    // MARK: Paquetes
    /// Nombre y tipo de la primera pregunta; `fin` = índice después de la pregunta.
    static func pregunta(_ q: [UInt8]) -> (nombre: String, tipo: UInt16, fin: Int)? {
        guard q.count >= 12, q[4] == 0, q[5] == 1 else { return nil }   // exactamente 1 pregunta
        var i = 12; var partes: [String] = []
        while i < q.count {
            let n = Int(q[i])
            if n == 0 { i += 1; break }
            if n & 0xC0 != 0 { return nil }
            guard i + 1 + n <= q.count else { return nil }
            partes.append(String(decoding: q[(i + 1)...(i + n)], as: UTF8.self))
            i += 1 + n
        }
        guard i + 4 <= q.count else { return nil }
        let tipo = UInt16(q[i]) << 8 | UInt16(q[i + 1])
        return (partes.joined(separator: ".").lowercased(), tipo, i + 4)
    }

    static func cabecera(_ q: [UInt8], rcode: UInt8, aa: Bool, respuestas: UInt16) -> [UInt8] {
        var h = Array(q[0..<12])
        let rd = q[2] & 0x01
        let opcode = q[2] & 0x78
        h[2] = 0x80 | opcode | (aa ? 0x04 : 0) | rd
        h[3] = 0x80 | (rcode & 0x0F)                  // RA + rcode
        h[4] = 0; h[5] = 1
        h[6] = UInt8(respuestas >> 8); h[7] = UInt8(respuestas & 0xFF)
        h[8] = 0; h[9] = 0; h[10] = 0; h[11] = 0
        return h
    }

    static func respuestaPropia(_ q: [UInt8], fin: Int, tipo: UInt16, ip: [UInt8]?) -> [UInt8] {
        let darA = (tipo == 1 || tipo == 255) && ip != nil
        var r = cabecera(q, rcode: 0, aa: true, respuestas: darA ? 1 : 0)
        r += q[12..<fin]
        if darA, let ip {
            r += [0xC0, 0x0C, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x3C, 0x00, 0x04] + ip  // TTL 60 s
        }
        return r
    }

    static func servfail(_ q: [UInt8], fin: Int) -> [UInt8] {
        cabecera(q, rcode: 2, aa: false, respuestas: 0) + q[12..<fin]
    }

    // MARK: Reenvío
    static func reenviar(_ q: [UInt8], o: Opciones) -> [UInt8]? {
        let s = socket(AF_INET, SOCK_DGRAM, 0)
        guard s >= 0 else { return nil }
        defer { close(s) }
        var enviados = 0
        var destinos: [in_addr_t] = []
        for up in o.reenviar {
            // «ip» o «ip:puerto» (el puerto sólo para pruebas).
            let partes = up.split(separator: ":")
            let puerto = partes.count == 2 ? UInt16(partes[1]) ?? 53 : 53
            var a = sockaddr_in(); a.sin_family = sa_family_t(AF_INET); a.sin_port = in_port_t(puerto).bigEndian
            guard inet_pton(AF_INET, String(partes[0]), &a.sin_addr) == 1 else { continue }
            destinos.append(a.sin_addr.s_addr)
            let n = q.withUnsafeBytes { b in
                withUnsafePointer(to: &a) { p in
                    p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sendto(s, b.baseAddress, q.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
                }
            }
            if n == q.count { enviados += 1 }
        }
        guard enviados > 0 else { return nil }      // sin ruta (sin red): falla al instante
        let limite = Date().addingTimeInterval(o.espera)
        var buf = [UInt8](repeating: 0, count: 4096)
        while true {
            let resta = limite.timeIntervalSinceNow
            if resta <= 0 { return nil }
            var tv = timeval(tv_sec: Int(resta), tv_usec: Int32((resta - floor(resta)) * 1_000_000))
            setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
            var de = sockaddr_in(); var len = socklen_t(MemoryLayout<sockaddr_in>.size)
            let n = buf.withUnsafeMutableBytes { b in
                withUnsafeMutablePointer(to: &de) { p in
                    p.withMemoryRebound(to: sockaddr.self, capacity: 1) { recvfrom(s, b.baseAddress, b.count, 0, $0, &len) }
                }
            }
            if n < 0 { return nil }
            if n >= 12, buf[0] == q[0], buf[1] == q[1], destinos.contains(de.sin_addr.s_addr) {
                return Array(buf[0..<n])
            }
        }
    }

    // MARK: Respuesta
    static func responder(_ q: [UInt8], destino: in_addr?, o: Opciones, salud: Salud) -> [UInt8]? {
        guard let p = pregunta(q) else { return nil }
        if o.nombres.contains(p.nombre) {
            salud.contarPropia()
            return respuestaPropia(q, fin: p.fin, tipo: p.tipo, ip: ipParaResponder(destino, o: o))
        }
        if !salud.intentar() { return servfail(q, fin: p.fin) }
        if let r = reenviar(q, o: o) { salud.resultado(true); return r }
        salud.resultado(false)
        return servfail(q, fin: p.fin)
    }

    static func ipParaResponder(_ destino: in_addr?, o: Opciones) -> [UInt8]? {
        if let f = o.ipFija {
            var a = in_addr()
            if inet_pton(AF_INET, f, &a) == 1 { return bytes(a) }
        }
        if let d = destino, !esLoopback(d), d.s_addr != 0 { return bytes(d) }
        return primeraIPLocal().map(bytes)
    }

    static func bytes(_ a: in_addr) -> [UInt8] { withUnsafeBytes(of: a.s_addr) { Array($0) } }
    static func esLoopback(_ a: in_addr) -> Bool { bytes(a).first == 127 }

    static func primeraIPLocal() -> in_addr? {
        var lista: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&lista) == 0, let primera = lista else { return nil }
        defer { freeifaddrs(lista) }
        var mejor: in_addr?
        var p: UnsafeMutablePointer<ifaddrs>? = primera
        while let i = p {
            defer { p = i.pointee.ifa_next }
            guard let sa = i.pointee.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET) else { continue }
            let nombre = String(cString: i.pointee.ifa_name)
            guard nombre.hasPrefix("en") else { continue }
            let a = sa.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr }
            if !esLoopback(a) { mejor = a; break }
        }
        return mejor
    }

    // MARK: Servidores
    static func correr(_ args: [String]) -> Int32 {
        let o = opciones(args)
        let salud = Salud()
        signal(SIGPIPE, SIG_IGN)
        let u = socket(AF_INET, SOCK_DGRAM, 0)
        guard u >= 0 else { log("ERROR: no pude crear el socket UDP"); return 1 }
        var uno: Int32 = 1
        setsockopt(u, IPPROTO_IP, IP_RECVDSTADDR, &uno, socklen_t(MemoryLayout<Int32>.size))
        guard enlazar(u, o.puerto) else {
            log("ERROR: el puerto UDP \(o.puerto) está ocupado o no se permite (\(String(cString: strerror(errno))))")
            return 3
        }
        log("DNS del nodo escuchando en 0.0.0.0:\(o.puerto)/udp · nombres: \(o.nombres.sorted().joined(separator: ", ")) → \(o.ipFija ?? "IP de esta Mac en la red") · lo demás → \(o.reenviar.joined(separator: ", "))")
        // TCP: casi nunca hace falta (respuestas pequeñas); si el puerto está ocupado se avisa y sigue.
        let t = socket(AF_INET, SOCK_STREAM, 0)
        setsockopt(t, SOL_SOCKET, SO_REUSEADDR, &uno, socklen_t(MemoryLayout<Int32>.size))
        if t >= 0, enlazar(t, o.puerto), listen(t, 16) == 0 {
            log("también en 0.0.0.0:\(o.puerto)/tcp")
            Thread.detachNewThread { aceptarTCP(t, o: o, salud: salud) }
        } else {
            log("AVISO: sin TCP en el puerto \(o.puerto) (\(String(cString: strerror(errno)))); UDP basta para el evento")
        }
        Thread.detachNewThread {
            while true { sleep(600); log("resumen: \(salud.resumen())") }
        }
        let cola = DispatchQueue(label: "dns", attributes: .concurrent)
        var buf = [UInt8](repeating: 0, count: 4096)
        var control = [UInt8](repeating: 0, count: 64)
        while true {
            var de = sockaddr_in()
            var destino: in_addr?
            let n: Int = buf.withUnsafeMutableBytes { b in
                control.withUnsafeMutableBytes { c in
                    withUnsafeMutablePointer(to: &de) { dp in
                        var iov = iovec(iov_base: b.baseAddress, iov_len: b.count)
                        return withUnsafeMutablePointer(to: &iov) { iovp in
                            var m = msghdr(msg_name: UnsafeMutableRawPointer(dp), msg_namelen: socklen_t(MemoryLayout<sockaddr_in>.size),
                                           msg_iov: iovp, msg_iovlen: 1, msg_control: c.baseAddress,
                                           msg_controllen: socklen_t(c.count), msg_flags: 0)
                            let r = recvmsg(u, &m, 0)
                            if r > 0 { destino = direccionDestino(&m) }
                            return r
                        }
                    }
                }
            }
            if n <= 0 { continue }
            let q = Array(buf[0..<n]); let quien = de; let dst = destino
            cola.async {
                guard let r = responder(q, destino: dst, o: o, salud: salud) else { return }
                var a = quien
                _ = r.withUnsafeBytes { b in
                    withUnsafePointer(to: &a) { p in
                        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sendto(u, b.baseAddress, r.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
                    }
                }
            }
        }
    }

    static func enlazar(_ s: Int32, _ puerto: UInt16) -> Bool {
        var a = sockaddr_in(); a.sin_family = sa_family_t(AF_INET); a.sin_port = puerto.bigEndian
        a.sin_addr.s_addr = INADDR_ANY
        return withUnsafePointer(to: &a) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
        }
    }

    static func direccionDestino(_ m: inout msghdr) -> in_addr? {
        guard m.msg_controllen >= socklen_t(MemoryLayout<cmsghdr>.size), let base = m.msg_control else { return nil }
        var off = 0
        let total = Int(m.msg_controllen)
        while off + MemoryLayout<cmsghdr>.size <= total {
            let h = base.advanced(by: off).assumingMemoryBound(to: cmsghdr.self).pointee
            if h.cmsg_level == IPPROTO_IP && h.cmsg_type == IP_RECVDSTADDR {
                let datos = base.advanced(by: off + MemoryLayout<cmsghdr>.size)
                var a = in_addr(); memcpy(&a, datos, MemoryLayout<in_addr>.size)
                return a
            }
            let largo = (Int(h.cmsg_len) + 3) & ~3
            if largo <= 0 { break }
            off += largo
        }
        return nil
    }

    static func aceptarTCP(_ t: Int32, o: Opciones, salud: Salud) {
        while true {
            let c = accept(t, nil, nil)
            if c < 0 { continue }
            DispatchQueue.global().async { atenderTCP(c, o: o, salud: salud) }
        }
    }

    static func leerTodo(_ c: Int32, _ n: Int) -> [UInt8]? {
        var r = [UInt8](repeating: 0, count: n); var hecho = 0
        while hecho < n {
            let k = r.withUnsafeMutableBytes { read(c, $0.baseAddress! + hecho, n - hecho) }
            if k <= 0 { return nil }
            hecho += k
        }
        return r
    }

    static func atenderTCP(_ c: Int32, o: Opciones, salud: Salud) {
        defer { close(c) }
        var tv = timeval(tv_sec: 10, tv_usec: 0)
        setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var local = sockaddr_in(); var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &local) { p in p.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(c, $0, &len) } }
        while let l = leerTodo(c, 2) {
            let n = Int(l[0]) << 8 | Int(l[1])
            guard n >= 12, let q = leerTodo(c, n), let r = responder(q, destino: local.sin_addr, o: o, salud: salud) else { return }
            let salida = [UInt8(r.count >> 8), UInt8(r.count & 0xFF)] + r
            let k = salida.withUnsafeBytes { write(c, $0.baseAddress, salida.count) }
            if k != salida.count { return }
        }
    }
}
