// «Dispositivos conectados»: monitor en vivo (al estilo Little Snitch) de los teléfonos que usan el
// nodo. Aquí sólo hay modelo y lógica pura (lectores + suma + reglas de bloqueo); la app junta los
// textos (registro de Caddy, `arp -an`, concesiones DHCP del router, API del nodo) y el motor
// escribe los bloqueos (bloqueos/bloqueados.caddy y, en el router, scripts por MAC).
import Foundation

// MARK: - Entradas

/// Una línea del registro de accesos JSON de Caddy 2.10 (`http.log.access`).
struct EntradaCaddy: Equatable {
    let ts: Date
    let ip: String            // request.client_ip (o remote_ip si no viene)
    let metodo: String
    let host: String
    let uri: String
    let agente: String?
    let status: Int
    let duracion: Double
    let tamano: Int
    var ruta: String { String(uri.split(separator: "?", maxSplits: 1).first ?? "") }
}

/// Concesión DHCP del router (OpenWrt/GL.iNet o MikroTik).
struct Lease: Equatable {
    let ip: String
    let mac: String
    let nombre: String?
    let expira: Date?
}

struct CuentaAPI: Equatable { let email: String?; let nombre: String?; var aLas: Date? = nil }

/// Un equipo según la API del nodo (`GET /api/v1/nodo/operador/dispositivos`):
/// {"generado", "minutos", "total", "dispositivos": [{"ip", "primera_vez", "ultima_vez",
///  "cuenta": {"email","nombre","a_las"}|null, "fallos_10min", "ultimo_fallo", "correos_distintos",
///  "correos", "sospechoso", "peticiones", "peticiones_por_minuto", "bloqueado_hasta"}]}.
/// Fechas ISO-8601 (también se aceptan segundos epoch). Todo opcional salvo la IP.
struct DispositivoAPI: Equatable {
    let ip: String
    let cuenta: CuentaAPI?
    let fallidos10min: Int
    let ultimoFallo: Date?
    let correosDistintos: Int
    let correos: [String]
    let primero: Date?
    let ultimo: Date?
    var sospechoso: Bool = false
    var peticiones: Int = 0
    var peticionesPorMinuto: Double = 0
    /// La API frenó el inicio de sesión de alguna cuenta desde este equipo hasta esta hora.
    var bloqueadoHasta: Date? = nil
}

// MARK: - Resultado

struct Alerta: Equatable, Hashable {
    enum Tipo: String, Codable { case muchosFallidos, escaneo, muchasPeticiones, muchasCuentas }
    let tipo: Tipo
    let texto: String
}

struct Dispositivo: Identifiable, Equatable {
    var id: String { ip }
    let ip: String
    let mac: String?
    let macPrivada: Bool          // MAC «privada» (bit de administración local): cambia por red
    let nombre: String?           // nombre del equipo según el DHCP del router
    let fabricante: String?       // sólo por tabla interna; nil con MAC privada
    let cuenta: CuentaAPI?
    let sinSesion: Bool
    let peticionesMin: Int
    let peticiones10min: Int
    let fallidos10min: Int
    let correosDistintos: Int
    let rutasDistintas404: Int    // en 10 min
    let agente: String?
    let ultimoVisto: Date?
    let bloqueado: Bool
    let alertas: [Alerta]
    /// `bloqueado_hasta` de la API (cuentas frenadas por demasiados intentos), si sigue vigente.
    var frenadoHasta: Date? = nil
    /// Notas informativas (grises), p. ej. «Cuenta(s) frenadas en este equipo».
    var notas: [String] = []
}

// MARK: - Lectores y suma

enum Dispositivos {
    // Umbrales (ventana de 10 minutos salvo que se diga otra cosa).
    static let ventana: TimeInterval = 600
    /// ≥ 8 inicios de sesión fallidos en 10 min → «muchosFallidos».
    static let umbralFallidos = 8
    /// ≥ 25 rutas distintas con 404 en 10 min (o cualquier ruta típica de escáner) → «escaneo».
    static let umbral404 = 25
    /// > 300 peticiones por minuto en los 2 últimos minutos seguidos → «muchasPeticiones».
    static let umbralPorMinuto = 300
    static let minutosSostenidos = 2
    /// > 3 correos distintos intentados desde el mismo equipo → «muchasCuentas».
    static let umbralCorreos = 3
    /// Rutas que sólo pide un escáner (el sitio del evento no tiene nada de esto).
    static let rutasEscaner = ["/wp-admin", "/wp-login.php", "/wp-content", "/wp-includes", "/xmlrpc.php", "/.env",
                               "/.git", "/.aws", "/.ds_store", "/phpmyadmin", "/pma", "/myadmin", "/cgi-bin/", "/vendor/phpunit",
                               "/actuator", "/server-status", "/boaform", "/hnap1", "/shell", "/config.php", "/admin.php", "/.well-known/security.txt.bak"]
    /// Rutas de inicio de sesión (para contar fallos vistos en Caddy si la API no los da).
    static let rutasLogin = ["/auth/login", "/api/v1/auth/login", "/api/v1/auth/token", "/login"]

    static func esRutaEscaner(_ ruta: String) -> Bool {
        let r = ruta.lowercased()
        return rutasEscaner.contains { r.hasPrefix($0) } || r.hasSuffix(".php") || r.hasSuffix(".asp") || r.hasSuffix(".aspx")
    }

    // Caddy
    static func caddy(_ texto: String) -> [EntradaCaddy] {
        texto.split(separator: "\n").compactMap { entradaCaddy(String($0)) }
    }

    static func entradaCaddy(_ linea: String) -> EntradaCaddy? {
        guard let a = linea.firstIndex(of: "{"),
              let o = (try? JSONSerialization.jsonObject(with: Data(linea[a...].utf8))) as? [String: Any],
              let req = o["request"] as? [String: Any] else { return nil }
        let ip = (req["client_ip"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? (req["remote_ip"] as? String) ?? ""
        guard !ip.isEmpty, let ts = fecha(o["ts"]) else { return nil }
        let headers = req["headers"] as? [String: Any]
        let ua = (headers?["User-Agent"] as? [String])?.first ?? (headers?["User-Agent"] as? String)
        return EntradaCaddy(ts: ts, ip: AnalisisIP.sinZona(ip), metodo: req["method"] as? String ?? "",
                            host: req["host"] as? String ?? "", uri: req["uri"] as? String ?? "",
                            agente: ua, status: entero(o["status"]) ?? 0,
                            duracion: (o["duration"] as? NSNumber)?.doubleValue ?? 0, tamano: entero(o["size"]) ?? 0)
    }

    static func fecha(_ v: Any?) -> Date? {
        if let n = v as? NSNumber { return Date(timeIntervalSince1970: n.doubleValue) }
        guard let s = v as? String else { return nil }
        if let d = Double(s) { return Date(timeIntervalSince1970: d) }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: s) { return d }
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: s) { return d }
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC")
        for formato in ["yyyy/MM/dd HH:mm:ss.SSS", "yyyy/MM/dd HH:mm:ss"] {
            f.dateFormat = formato
            if let d = f.date(from: s) { return d }
        }
        return nil
    }

    static func entero(_ v: Any?) -> Int? {
        if let n = v as? NSNumber { return n.intValue }
        if let s = v as? String { return Int(s) }
        return nil
    }

    /// MAC en minúsculas con dos dígitos por octeto (macOS escribe «a:b:c:…»).
    static func normalizarMAC(_ mac: String) -> String? {
        let p = mac.lowercased().split(whereSeparator: { $0 == ":" || $0 == "-" })
        guard p.count == 6, p.allSatisfy({ (1...2).contains($0.count) && $0.allSatisfy(\.isHexDigit) }) else { return nil }
        return p.map { $0.count == 1 ? "0" + $0 : String($0) }.joined(separator: ":")
    }

    /// `arp -an`: «? (192.168.8.120) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]» → IP: MAC.
    static func arp(_ texto: String) -> [String: String] {
        var r: [String: String] = [:]
        for l in texto.split(separator: "\n") {
            guard let a = l.firstIndex(of: "("), let b = l.firstIndex(of: ")"), a < b else { continue }
            let ip = String(l[l.index(after: a)..<b])
            let t = l[b...].split(separator: " ")
            guard let i = t.firstIndex(of: "at"), i + 1 < t.endIndex, let mac = normalizarMAC(String(t[i + 1])) else { continue }
            if mac == "ff:ff:ff:ff:ff:ff" || mac.hasPrefix("01:00:5e") || mac.hasPrefix("33:33") { continue }
            r[ip] = mac
        }
        return r
    }

    /// OpenWrt / GL.iNet `/tmp/dhcp.leases`: «epoch MAC IP nombre clientid» («*» = sin nombre).
    static func leasesOpenWrt(_ texto: String) -> [Lease] {
        texto.split(separator: "\n").compactMap { l in
            let t = l.split(separator: " ").map(String.init)
            guard t.count >= 3, let mac = normalizarMAC(t[1]), AnalisisIP.esIPv4(t[2]) || t[2].contains(":") else { return nil }
            let nombre = t.count >= 4 && t[3] != "*" ? t[3] : nil
            let exp = Double(t[0]).flatMap { $0 > 0 ? Date(timeIntervalSince1970: $0) : nil }
            return Lease(ip: t[2], mac: mac, nombre: nombre, expira: exp)
        }
    }

    /// MikroTik `/ip dhcp-server lease print terse`: «0 D address=… mac-address=… host-name=…».
    static func leasesMikroTik(_ texto: String) -> [Lease] {
        texto.split(separator: "\n").compactMap { l in
            let kv = paresClaveValor(String(l))
            guard let ip = kv["active-address"] ?? kv["address"],
                  let mac = normalizarMAC(kv["active-mac-address"] ?? kv["mac-address"] ?? "") else { return nil }
            let nombre = kv["host-name"].flatMap { $0.isEmpty ? nil : $0 }
            return Lease(ip: ip, mac: mac, nombre: nombre, expira: nil)
        }
    }

    static func paresClaveValor(_ s: String) -> [String: String] {
        var r: [String: String] = [:]
        var i = s.startIndex
        while i < s.endIndex {
            while i < s.endIndex, s[i] == " " || s[i] == "\t" { i = s.index(after: i) }
            let ini = i
            while i < s.endIndex, s[i] != " ", s[i] != "=" { i = s.index(after: i) }
            let clave = String(s[ini..<i])
            guard i < s.endIndex, s[i] == "=" else { continue }
            i = s.index(after: i)
            var valor = ""
            if i < s.endIndex, s[i] == "\"" {
                i = s.index(after: i)
                while i < s.endIndex, s[i] != "\"" { valor.append(s[i]); i = s.index(after: i) }
                if i < s.endIndex { i = s.index(after: i) }
            } else {
                while i < s.endIndex, s[i] != " " { valor.append(s[i]); i = s.index(after: i) }
            }
            r[clave] = valor
        }
        return r
    }

    /// Respuesta de la API del nodo: `{"dispositivos":[…]}` o directamente `[…]`. Tolerante.
    static func api(_ datos: Data) -> [DispositivoAPI] {
        api(objeto: try? JSONSerialization.jsonObject(with: datos))
    }

    static func api(objeto raiz: Any?) -> [DispositivoAPI] {
        let lista = (raiz as? [String: Any])?["dispositivos"] as? [[String: Any]] ?? raiz as? [[String: Any]] ?? []
        return lista.compactMap { d in
            guard let ip = d["ip"] as? String, !ip.isEmpty else { return nil }
            let c = d["cuenta"] as? [String: Any]
            let correos = (d["correos"] as? [String]) ?? []
            var x = DispositivoAPI(ip: AnalisisIP.sinZona(ip),
                                   cuenta: c.map { CuentaAPI(email: $0["email"] as? String, nombre: $0["nombre"] as? String, aLas: fecha($0["a_las"])) },
                                   fallidos10min: entero(d["fallos_10min"]) ?? entero(d["fallidos_10min"]) ?? 0,
                                   ultimoFallo: fecha(d["ultimo_fallo"]),
                                   correosDistintos: max(entero(d["correos_distintos"]) ?? 0, Set(correos).count),
                                   correos: correos,
                                   primero: fecha(d["primera_vez"]) ?? fecha(d["primero"]),
                                   ultimo: fecha(d["ultima_vez"]) ?? fecha(d["ultimo"]))
            x.sospechoso = (d["sospechoso"] as? Bool) ?? ((d["sospechoso"] as? NSNumber)?.boolValue ?? false)
            x.peticiones = entero(d["peticiones"]) ?? 0
            x.peticionesPorMinuto = (d["peticiones_por_minuto"] as? NSNumber)?.doubleValue ?? Double(d["peticiones_por_minuto"] as? String ?? "") ?? 0
            x.bloqueadoHasta = fecha(d["bloqueado_hasta"])
            return x
        }
    }

    /// Lo que imprime `nodo-mac.sh dispositivos`:
    /// {"api": <respuesta de la API>, "arp": "<texto de arp -an>", "acceso": "<ruta del registro de Caddy>",
    ///  "mi_ip": "…", "router": "…", "gateway": "…"}. Si falta `router` se usa `gateway`.
    static func leerMotor(_ data: Data) -> (api: [DispositivoAPI], arp: [String: String], rutaAcceso: String, miIP: String, router: String) {
        let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        var apiObj: Any? = o["api"]
        if let s = apiObj as? String { apiObj = try? JSONSerialization.jsonObject(with: Data(s.utf8)) }
        let router = (o["router"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? (o["gateway"] as? String) ?? ""
        return (api(objeto: apiObj), arp(o["arp"] as? String ?? ""), o["acceso"] as? String ?? "", o["mi_ip"] as? String ?? "", router)
    }

    /// Últimas `maxLineas` líneas completas de un archivo, leyendo sólo los últimos `maxBytes`
    /// (el registro de Caddy puede pesar 20 MB). Si no existe, [].
    static func ultimasLineas(ruta: String, maxBytes: Int = 4 << 20, maxLineas: Int = 20000) -> [String] {
        guard let fh = FileHandle(forReadingAtPath: ruta) else { return [] }
        defer { try? fh.close() }
        let tam = (try? fh.seekToEnd()) ?? 0
        let desde = tam > UInt64(maxBytes) ? tam - UInt64(maxBytes) : 0
        try? fh.seek(toOffset: desde)
        let datos = (try? fh.readToEnd()) ?? Data()
        var lineas = String(decoding: datos, as: UTF8.self).split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        if desde > 0, !lineas.isEmpty { lineas.removeFirst() }   // la primera puede venir cortada
        return lineas.count > maxLineas ? Array(lineas.suffix(maxLineas)) : lineas
    }

    // MAC
    /// Bit «administrado localmente» (0x02 del primer octeto): iPhone y Android usan MAC privadas.
    static func esMACPrivada(_ mac: String) -> Bool {
        guard let n = normalizarMAC(mac), let b = UInt8(n.prefix(2), radix: 16) else { return false }
        return b & 0x02 != 0
    }

    /// Tabla mínima de prefijos (OUI) de fabricantes comunes de teléfonos/routers. Parcial a
    /// propósito: cada prefijo se comprobó en el registro IEEE (vía maclookup.app, oct 2026).
    static let oui: [String: String] = [
        "f0:18:98": "Apple", "ac:bc:32": "Apple", "a4:83:e7": "Apple", "3c:22:fb": "Apple",
        "00:16:32": "Samsung", "00:12:fb": "Samsung", "8c:77:12": "Samsung",
        "64:09:80": "Xiaomi", "f8:a4:5f": "Xiaomi", "28:6c:07": "Xiaomi",
        "9c:d9:17": "Motorola", "e8:91:20": "Motorola",
        "00:e0:fc": "Huawei", "48:46:fb": "Huawei",
        "3c:5a:b4": "Google", "f4:f5:d8": "Google",
        "94:65:2d": "OnePlus", "c0:ee:fb": "OnePlus",
        "a4:3d:78": "Oppo", "00:9c:c0": "Vivo", "0c:90:43": "Realme", "00:a3:07": "Honor",
        "04:01:bb": "Tecno", "00:5e:0c": "Nokia (HMD)", "00:19:c6": "ZTE", "00:1e:75": "LG",
        "fc:f1:52": "Sony", "50:c7:bf": "TP-Link", "94:83:c4": "GL.iNet",
    ]

    static func fabricante(_ mac: String?) -> String? {
        guard let m = mac.flatMap(normalizarMAC), !esMACPrivada(m) else { return nil }
        return oui[String(m.prefix(8))]
    }

    // Suma
    static func agregar(caddy: [EntradaCaddy], arp: [String: String], leases: [Lease], api: [DispositivoAPI],
                        bloqueados: Set<String>, ahora: Date) -> [Dispositivo] {
        let desde = ahora.addingTimeInterval(-ventana)
        let porIP = Dictionary(grouping: caddy.filter { !AnalisisIP.esLoopback($0.ip) && $0.ruta != "/nodo-salud" }, by: { $0.ip })
        let leasePorIP = Dictionary(leases.map { ($0.ip, $0) }, uniquingKeysWith: { a, _ in a })
        let apiPorIP = Dictionary(api.map { ($0.ip, $0) }, uniquingKeysWith: { a, _ in a })
        let bloq = Set(bloqueados.map { normalizarMAC($0) ?? $0 })
        var ips = Set(porIP.keys).union(apiPorIP.keys).union(leasePorIP.keys)
        ips = ips.filter { !AnalisisIP.esLoopback($0) }

        var r: [Dispositivo] = []
        for ip in ips {
            let todas = porIP[ip] ?? []
            let rec = todas.filter { $0.ts >= desde && $0.ts <= ahora.addingTimeInterval(5) }
            let a = apiPorIP[ip]
            let l = leasePorIP[ip]
            let mac = arp[ip].flatMap(normalizarMAC) ?? l?.mac
            let min1 = rec.filter { $0.ts > ahora.addingTimeInterval(-60) }.count
            var sostenido = true
            for k in 0..<minutosSostenidos {
                let fin = ahora.addingTimeInterval(-60 * Double(k)), ini = fin.addingTimeInterval(-60)
                if rec.filter({ $0.ts > ini && $0.ts <= fin }).count <= umbralPorMinuto { sostenido = false }
            }
            let rutas404 = Set(rec.filter { $0.status == 404 }.map { $0.ruta })
            let escaner = Set(rec.map { $0.ruta }.filter(esRutaEscaner))
            let fallCaddy = rec.filter { e in e.metodo == "POST" && [400, 401, 403, 429].contains(e.status)
                && rutasLogin.contains { e.ruta.hasPrefix($0) } }.count
            let fallidos = max(a?.fallidos10min ?? 0, fallCaddy)
            let correos = a?.correosDistintos ?? 0
            var alertas: [Alerta] = []
            if fallidos >= umbralFallidos {
                alertas.append(Alerta(tipo: .muchosFallidos, texto: "\(fallidos) contraseñas equivocadas en 10 minutos: alguien podría estar adivinando contraseñas."))
            }
            if rutas404.count >= umbral404 || !escaner.isEmpty {
                let ej = escaner.sorted().prefix(3).joined(separator: ", ")
                alertas.append(Alerta(tipo: .escaneo, texto: escaner.isEmpty
                    ? "Pidió \(rutas404.count) páginas que no existen en 10 minutos: parece un programa que busca fallas."
                    : "Buscó páginas que sólo busca un programa de ataque (\(ej)). No es un teléfono normal."))
            }
            if sostenido {
                alertas.append(Alerta(tipo: .muchasPeticiones, texto: "Más de \(umbralPorMinuto) peticiones por minuto sin parar (\(min1) en el último minuto): puede estar saturando el nodo."))
            }
            if correos > umbralCorreos || a?.sospechoso == true {
                alertas.append(Alerta(tipo: .muchasCuentas, texto: correos > umbralCorreos
                    ? "Intentó entrar con \(correos) correos distintos: alguien podría estar probando cuentas ajenas."
                    : "El nodo marcó este equipo como sospechoso (intentos con varias cuentas)."))
            }
            var notas: [String] = []
            let frenado = a?.bloqueadoHasta.flatMap { $0 > ahora ? $0 : nil }
            if let f = frenado {
                let min = max(1, Int((f.timeIntervalSince(ahora) / 60).rounded(.up)))
                notas.append("Cuenta(s) frenadas en este equipo por demasiados intentos (unos \(min) min más).")
            }
            let vistos = todas.map { $0.ts } + [a?.ultimo, a?.ultimoFallo].compactMap { $0 }
            let ultimoReq = todas.max { $0.ts < $1.ts }
            r.append(Dispositivo(ip: ip, mac: mac, macPrivada: mac.map(esMACPrivada) ?? false, nombre: l?.nombre,
                                 fabricante: fabricante(mac), cuenta: a?.cuenta, sinSesion: a?.cuenta == nil,
                                 peticionesMin: min1, peticiones10min: rec.count, fallidos10min: fallidos,
                                 correosDistintos: correos, rutasDistintas404: rutas404.count,
                                 agente: ultimoReq?.agente, ultimoVisto: vistos.max(),
                                 bloqueado: bloq.contains(ip) || (mac.map { bloq.contains($0) } ?? false),
                                 alertas: alertas, frenadoHasta: frenado, notas: notas))
        }
        return r.sorted { x, y in
            if x.alertas.count != y.alertas.count { return x.alertas.count > y.alertas.count }
            let tx = x.ultimoVisto?.timeIntervalSince1970 ?? -1, ty = y.ultimoVisto?.timeIntervalSince1970 ?? -1
            if tx != ty { return tx > ty }
            return ReglasBloqueo.claveOrden(x.ip) < ReglasBloqueo.claveOrden(y.ip)
        }
    }
}

// MARK: - Bloqueos (lógica pura; el motor escribe archivos y scripts del router)

enum ReglasBloqueo {
    enum Validacion: Equatable { case valida, rechazada(String) }

    /// Sólo equipos de la red local del evento: IPv4 privada (10/8, 172.16/12, 192.168/16) o IPv6
    /// de enlace local (fe80::/10). Nunca esta Mac, el router, 127.0.0.1 ni la difusión.
    static func validar(ip: String, propias: [String] = [], router: String? = nil) -> Validacion {
        let ip = AnalisisIP.sinZona(ip.trimmingCharacters(in: .whitespaces))
        if ip.isEmpty { return .rechazada("Falta la dirección IP.") }
        if AnalisisIP.esLoopback(ip) { return .rechazada("Esa es la propia Mac (127.0.0.1).") }
        if propias.map(AnalisisIP.sinZona).contains(ip) { return .rechazada("Esa es la IP de esta Mac: bloquearla apagaría el nodo para todos.") }
        if let r = router, AnalisisIP.sinZona(r) == ip { return .rechazada("Ese es el router: bloquearlo dejaría a todos sin red.") }
        if let o = AnalisisIP.octetos(ip) {
            if ip == "255.255.255.255" || o[3] == 255 { return .rechazada("Esa es la dirección de difusión de la red, no un equipo.") }
            if o[3] == 0 { return .rechazada("Esa es la dirección de la red, no un equipo.") }
            guard AnalisisIP.esPrivadaV4(ip) else { return .rechazada("Sólo se pueden bloquear equipos de la red local del evento.") }
            return .valida
        }
        if ip.contains(":") {
            let partes = ip.lowercased().split(separator: ":", omittingEmptySubsequences: false)
            guard ip.lowercased().allSatisfy({ $0.isHexDigit || $0 == ":" }), partes.count >= 3,
                  let primero = UInt16(partes[0], radix: 16), primero & 0xffc0 == 0xfe80 else {
                return .rechazada("Sólo se pueden bloquear equipos de la red local del evento.")
            }
            return .valida
        }
        return .rechazada("«\(ip)» no es una dirección IP.")
    }

    /// Contenido de bloqueos/bloqueados.caddy (importado en cada sitio por `import bloqueos_nodo`;
    /// el Caddyfile tiene `order error before handle`, así que el 403 va antes que todo). Las IP que
    /// no son válidas se ignoran (nunca llega texto libre al Caddyfile).
    static func caddySnippet(ips: [String]) -> String {
        var vistas = Set<String>()
        let limpias = ips.map { AnalisisIP.sinZona($0.trimmingCharacters(in: .whitespaces)) }
            .filter { validar(ip: $0) == .valida && vistas.insert($0).inserted }
            .sorted { claveOrden($0) < claveOrden($1) }
        guard !limpias.isEmpty else { return "# Sin bloqueos (la app «Nodo Camporee» reescribe este archivo).\n" }
        return """
        @nodo_bloqueados remote_ip \(limpias.joined(separator: " "))
        error @nodo_bloqueados "Este equipo fue bloqueado por la coordinación del evento" 403

        """
    }

    /// Una línea JSON para el registro de auditoría (quién bloqueó qué, cuándo y dónde).
    static func lineaAuditoria(accion: String, ip: String, mac: String?, motivo: String, donde: [String], ahora: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        var o: [String: Any] = ["ts": f.string(from: ahora), "epoch": Int(ahora.timeIntervalSince1970),
                                "accion": accion, "ip": ip, "motivo": motivo, "donde": donde]
        o["mac"] = mac.flatMap(Dispositivos.normalizarMAC) ?? NSNull()
        let datos = (try? JSONSerialization.data(withJSONObject: o, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data("{}".utf8)
        return String(decoding: datos, as: UTF8.self)
    }

    /// Orden natural de IPs (192.168.8.9 antes que 192.168.8.10).
    static func claveOrden(_ ip: String) -> String {
        if let o = AnalisisIP.octetos(ip) { return o.map { String(format: "%03d", $0) }.joined(separator: ".") }
        return "~" + ip
    }
}
