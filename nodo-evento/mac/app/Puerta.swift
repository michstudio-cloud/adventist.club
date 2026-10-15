// «Puerta» del nodo: relevo TCP con la IP REAL de cada teléfono.
//
// Con Colima, Docker publica los puertos a través del reenviador de Lima (por SSH): a Caddy todas
// las conexiones le llegaban desde la misma IP interna, así que la API veía a todos los teléfonos
// como uno solo (límites por dispositivo, registros y bloqueos no servían). Ahora:
//
//   teléfono ──► Mac :80/:443 (este relevo, ejecutable de la app)
//                 └─► 127.0.0.1:8080/8443 (Colima → Caddy) con una línea «PROXY TCP4 …» (protocolo
//                     PROXY v1 de HAProxy, documento público) que dice quién es el cliente
//   Caddy (listener_wrappers › proxy_protocol) usa esa IP como la del cliente: registros, límites
//   por dispositivo de la API (X-Forwarded-For → Next → X-Proxy-Client-IP) y bloqueos por IP.
//
// Sin root: un proceso normal puede escuchar en puertos < 1024 si escucha en TODAS las direcciones
// (igual que el DNS de la Mac). Corre como LaunchAgent (`Nodo Camporee --puerta`, KeepAlive) mientras
// el nodo está encendido. Los puertos de Colima sólo escuchan en 127.0.0.1: desde la red, el único
// camino a Caddy es este relevo (nadie puede falsificar la línea PROXY).
//
// Bloqueos «a nivel del nodo»: si la IP del cliente está en `bloqueados.json`, la conexión se cierra
// al instante (además Caddy contesta 403 con su propia lista: doble barrera).
import Foundation
import Network

enum Puerta {
    struct Opciones {
        var pares: [(UInt16, UInt16)] = [(80, 8080), (443, 8443)]   // escucha → destino en 127.0.0.1
        var bloqueados: String? = nil                               // ruta de bloqueados.json
        var estado: String? = nil                                   // ruta de puerta.json (contadores)
    }

    static func opciones(_ args: [String]) -> Opciones {
        var o = Opciones()
        var i = 0
        var pares: [(UInt16, UInt16)] = []
        while i < args.count {
            let v = i + 1 < args.count ? args[i + 1] : ""
            switch args[i] {
            case "--par":   // 80:8080
                let p = v.split(separator: ":").compactMap { UInt16($0) }
                if p.count == 2 { pares.append((p[0], p[1])) }
                i += 1
            case "--bloqueados": o.bloqueados = v; i += 1
            case "--estado": o.estado = v; i += 1
            default: break
            }
            i += 1
        }
        if !pares.isEmpty { o.pares = pares }
        return o
    }

    static func log(_ s: String) {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        print("\(f.string(from: Date())) \(s)"); fflush(stdout)
    }

    /// Normaliza la IP del cliente: quita «%en0» y convierte ::ffff:a.b.c.d en a.b.c.d.
    static func limpiarIP(_ s: String) -> String {
        var ip = s
        if let p = ip.firstIndex(of: "%") { ip = String(ip[..<p]) }
        if ip.lowercased().hasPrefix("::ffff:"), ip.contains(".") { ip = String(ip.dropFirst(7)) }
        return ip
    }

    /// Línea del protocolo PROXY v1. TCP4 si ambos extremos son IPv4; si no, TCP6 (con las IPv4
    /// escritas como ::ffff:a.b.c.d, como pide la especificación).
    static func lineaPROXY(cliente: String, puertoCliente: UInt16, local: String, puertoLocal: UInt16) -> String {
        let c = limpiarIP(cliente), l = limpiarIP(local)
        let v4 = !c.contains(":") && !l.contains(":")
        if v4 { return "PROXY TCP4 \(c) \(l) \(puertoCliente) \(puertoLocal)\r\n" }
        let c6 = c.contains(":") ? c : "::ffff:\(c)"
        let l6 = l.contains(":") ? l : "::ffff:\(l)"
        return "PROXY TCP6 \(c6) \(l6) \(puertoCliente) \(puertoLocal)\r\n"
    }

    // MARK: Bloqueos y contadores

    final class Compartido: @unchecked Sendable {
        private let l = NSLock()
        private var bloqueados = Set<String>()
        private var mtime: Date?
        private var conexiones = 0, rechazadas = 0, activas = 0
        let rutaBloqueos: String?
        init(_ ruta: String?) { rutaBloqueos = ruta }

        func recargar() {
            guard let r = rutaBloqueos else { return }
            let m = (try? FileManager.default.attributesOfItem(atPath: r))?[.modificationDate] as? Date
            l.lock(); let igual = (m == mtime); l.unlock()
            if igual { return }
            var nuevos = Set<String>()
            if let d = FileManager.default.contents(atPath: r),
               let arr = try? JSONSerialization.jsonObject(with: d) as? [[String: Any]] {
                for x in arr { if let ip = x["ip"] as? String, !ip.isEmpty { nuevos.insert(Puerta.limpiarIP(ip)) } }
            }
            l.lock(); bloqueados = nuevos; mtime = m; l.unlock()
            Puerta.log("bloqueados: \(nuevos.count)")
        }
        func bloqueado(_ ip: String) -> Bool { l.lock(); defer { l.unlock() }; return bloqueados.contains(ip) }
        func abrir() { l.lock(); conexiones += 1; activas += 1; l.unlock() }
        func cerrar() { l.lock(); activas -= 1; l.unlock() }
        func rechazar() { l.lock(); rechazadas += 1; l.unlock() }
        func json() -> [String: Any] {
            l.lock(); defer { l.unlock() }
            return ["conexiones": conexiones, "rechazadas": rechazadas, "activas": activas, "bloqueados": bloqueados.count,
                    "t": Date().timeIntervalSince1970, "pid": Int(getpid())]
        }
    }

    // MARK: Relevo

    final class Tubo: @unchecked Sendable {
        let entrada: NWConnection
        let salida: NWConnection
        let compartido: Compartido
        private var cerrado = false
        private let l = NSLock()
        init(_ e: NWConnection, _ s: NWConnection, _ c: Compartido) { entrada = e; salida = s; compartido = c }

        func cerrar() {
            l.lock(); let ya = cerrado; cerrado = true; l.unlock()
            if ya { return }
            entrada.cancel(); salida.cancel(); compartido.cerrar()
        }

        func bombear(de a: NWConnection, a b: NWConnection) {
            a.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [self] data, _, fin, error in
                if let data, !data.isEmpty {
                    b.send(content: data, completion: .contentProcessed { [self] e in
                        if e != nil { cerrar(); return }
                        if fin { b.send(content: nil, contentContext: .finalMessage, isComplete: true, completion: .idempotent) }
                        else { bombear(de: a, a: b) }
                    })
                } else if fin || error != nil {
                    if error != nil { cerrar(); return }
                    // Medio cierre: avisa al otro lado y espera a que termine.
                    b.send(content: nil, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { _ in })
                } else {
                    bombear(de: a, a: b)
                }
            }
        }
    }

    static func hostPuerto(_ e: NWEndpoint?) -> (String, UInt16)? {
        guard case let .hostPort(h, p)? = e else { return nil }
        let host: String
        switch h {
        case .ipv4(let a): host = "\(a)"
        case .ipv6(let a): host = "\(a)"
        case .name(let n, _): host = n
        @unknown default: host = "\(h)"
        }
        return (limpiarIP(host), p.rawValue)
    }

    static func atender(_ c: NWConnection, destino: UInt16, puertoLocal: UInt16, compartido: Compartido, cola: DispatchQueue) {
        guard let (ip, puerto) = hostPuerto(c.endpoint) else { c.cancel(); return }
        if compartido.bloqueado(ip) {
            compartido.rechazar()
            c.cancel()
            return
        }
        compartido.abrir()
        let salida = NWConnection(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: destino)!, using: .tcp)
        let tubo = Tubo(c, salida, compartido)
        var localIP = "127.0.0.1"
        c.stateUpdateHandler = { st in
            if ProcessInfo.processInfo.environment["PUERTA_DEPURAR"] != nil { log("cliente \(ip): \(st)") }
            switch st {
            case .ready:
                if let (lip, _) = hostPuerto(c.currentPath?.localEndpoint) { localIP = lip }
                salida.start(queue: cola)
            case .failed, .cancelled: tubo.cerrar()
            default: break
            }
        }
        salida.stateUpdateHandler = { st in
            if ProcessInfo.processInfo.environment["PUERTA_DEPURAR"] != nil { log("caddy (\(ip)): \(st)") }
            switch st {
            case .ready:
                let linea = lineaPROXY(cliente: ip, puertoCliente: puerto, local: localIP, puertoLocal: puertoLocal)
                salida.send(content: linea.data(using: .ascii), completion: .contentProcessed { e in
                    if e != nil { tubo.cerrar(); return }
                    tubo.bombear(de: c, a: salida)
                    tubo.bombear(de: salida, a: c)
                })
            case .failed, .cancelled: tubo.cerrar()
            case .waiting: tubo.cerrar()   // Caddy no está arriba: no dejar al teléfono colgado
            default: break
            }
        }
        c.start(queue: cola)
    }

    static func correr(_ args: [String]) -> Int32 {
        signal(SIGPIPE, SIG_IGN)
        let o = opciones(args)
        let compartido = Compartido(o.bloqueados)
        compartido.recargar()
        let cola = DispatchQueue(label: "puerta", attributes: .concurrent)
        var escuchas: [NWListener] = []
        for (puerto, destino) in o.pares {
            let params = NWParameters.tcp
            params.allowLocalEndpointReuse = true
            guard let p = NWEndpoint.Port(rawValue: puerto), let l = try? NWListener(using: params, on: p) else {
                log("no pude escuchar en el puerto \(puerto)")
                return 1
            }
            l.newConnectionHandler = { c in atender(c, destino: destino, puertoLocal: puerto, compartido: compartido, cola: cola) }
            l.stateUpdateHandler = { st in
                switch st {
                case .ready: log("escuchando en :\(puerto) → 127.0.0.1:\(destino)")
                case .failed(let e): log("falló :\(puerto): \(e)"); exit(1)   // launchd lo vuelve a intentar
                default: break
                }
            }
            l.start(queue: cola)
            escuchas.append(l)
        }
        let t = DispatchSource.makeTimerSource(queue: .global())
        t.schedule(deadline: .now() + 2, repeating: 2)
        t.setEventHandler {
            compartido.recargar()
            if let ruta = o.estado, let d = try? JSONSerialization.data(withJSONObject: compartido.json()) {
                try? d.write(to: URL(fileURLWithPath: ruta), options: .atomic)
            }
        }
        t.resume()
        withExtendedLifetime((escuchas, t)) { dispatchMain() }
    }
}
