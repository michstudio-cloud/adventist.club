// «Interferencias»: otras apps o ajustes de esta Mac que pueden impedir que los teléfonos lleguen
// al nodo o que cambian el DNS (VPN, filtros de red, firewall, DNS manual).
//
// Sólo lee. Órdenes de Apple documentadas: ifconfig, netstat, scutil, systemextensionsctl, ps,
// socketfilterfw --get*/--listapps, networksetup -getdnsservers. Nunca recomienda desinstalar:
// sólo qué apagar durante el evento o qué permitir.
//
// Identificadores que se buscan (leídos SÓLO de los Info.plist de las apps, sin abrir su código):
//   NordVPN 10.11: app com.nordvpn.macos (ejecutable «NordVPN»), extensión de sistema
//     com.nordvpn.macos.Shield (app-proxy, «NordVPN protection»), túneles com.nordvpn.macos.NordLynx,
//     com.nordvpn.macos.NetworkTunnelExtension (OpenVPN), com.nordvpn.macos.NordWhisperTunnel.
//   Little Snitch 6.4: app at.obdev.littlesnitch, extensión at.obdev.littlesnitch.networkextension
//     (filtro de datos + proxy de DNS), at.obdev.littlesnitch.endpointsecurity, «Little Snitch
//     Agent» (at.obdev.littlesnitch.agent), «Little Snitch Network Monitor».
// Los demás (Tailscale, WireGuard, Proton, Mullvad, WARP, LuLu…) vienen de su documentación pública
// o de salidas reales de systemextensionsctl; ver pruebas/muestras/LEEME.txt.
import Foundation

enum Interferencias {
    struct Muestras {
        var ifconfig = ""            // ifconfig
        var netstatRutas = ""        // netstat -rn -f inet
        var scutilDNS = ""           // scutil --dns
        var scutilNC = ""            // scutil --nc list
        var sistemaExt = ""          // systemextensionsctl list
        var procesos = ""            // ps -axo comm=
        var firewallGlobal = ""      // socketfilterfw --getglobalstate
        var firewallApps = ""        // socketfilterfw --listapps
        var firewallSigilo = ""      // socketfilterfw --getstealthmode
        var gatewayIP: String? = nil
        var resolversRouter: [String] = []
        var firewallBloqueoTotal = "" // socketfilterfw --getblockall
        var dnsManual = ""           // networksetup -getdnsservers <servicio> (uno o varios)
        var limactlRuta: String? = nil
    }

    // MARK: - Lectores (puros)

    struct Interfaz: Equatable { let nombre: String; let arriba: Bool; let ipv4: [String]; let ipv6: [String] }

    /// Bloques de `ifconfig`. `ipv6` sólo trae direcciones globales (sin fe80::).
    static func interfaces(_ texto: String) -> [Interfaz] {
        var res: [Interfaz] = []
        var nombre: String?; var arriba = false; var v4: [String] = []; var v6: [String] = []
        func cerrar() { if let n = nombre { res.append(Interfaz(nombre: n, arriba: arriba, ipv4: v4, ipv6: v6)) } }
        for l in texto.split(separator: "\n", omittingEmptySubsequences: false) {
            let s = String(l)
            if let c = s.first, !c.isWhitespace, let dos = s.firstIndex(of: ":") {
                cerrar()
                nombre = String(s[..<dos]); v4 = []; v6 = []
                let flags = s.range(of: "<").flatMap { a in s.range(of: ">", range: a.upperBound..<s.endIndex).map { String(s[a.upperBound..<$0.lowerBound]) } } ?? ""
                arriba = flags.split(separator: ",").contains("UP")
                continue
            }
            let t = s.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
            if t.count >= 2, t[0] == "inet" { v4.append(t[1]) }
            if t.count >= 2, t[0] == "inet6" {
                let ip = AnalisisIP.sinZona(t[1])
                if !AnalisisIP.esLinkLocalV6(ip) && ip != "::1" { v6.append(ip) }
            }
        }
        cerrar()
        return res
    }

    static func esTunel(_ nombre: String) -> Bool {
        ["utun", "ipsec", "ppp", "tun", "tap", "wg"].contains { nombre.hasPrefix($0) }
    }

    /// Túneles por los que sale la ruta por defecto (o las rutas partidas 0/1 + 128.0/1 que usan
    /// WireGuard/NordLynx/OpenVPN para tapar la ruta por defecto).
    static func tunelesConRutaPorDefecto(_ netstat: String) -> [String] {
        var r: [String] = []
        for l in netstat.split(separator: "\n") {
            let t = l.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
            guard t.count >= 4 else { continue }
            let destino = t[0]
            guard ["default", "0/1", "128.0/1", "0.0.0.0/1", "128.0.0.0/1"].contains(destino) else { continue }
            if let netif = t.first(where: { esTunel($0) }), !r.contains(netif) { r.append(netif) }
        }
        return r
    }

    struct Resolver: Equatable {
        let seccion: Int          // 0 = principal, 1 = «for scoped queries»
        let numero: Int
        let dominio: String?
        let servidores: [String]
        let interfaz: String?
        let busqueda: [String]
    }

    static func resolvers(_ texto: String) -> [Resolver] {
        var res: [Resolver] = []
        var seccion = 0; var numero: Int?; var dominio: String?; var servidores: [String] = []
        var interfaz: String?; var busqueda: [String] = []
        func cerrar() {
            if let n = numero { res.append(Resolver(seccion: seccion, numero: n, dominio: dominio, servidores: servidores, interfaz: interfaz, busqueda: busqueda)) }
            numero = nil; dominio = nil; servidores = []; interfaz = nil; busqueda = []
        }
        for l in texto.split(separator: "\n") {
            let s = l.trimmingCharacters(in: .whitespaces)
            if s.hasPrefix("DNS configuration") { cerrar(); seccion = s.contains("scoped") ? 1 : 0 }
            else if s.hasPrefix("resolver #") { cerrar(); numero = Int(s.dropFirst("resolver #".count)) }
            else if let v = valor(s, "domain") { dominio = v }
            else if s.hasPrefix("nameserver["), let v = s.split(separator: ":", maxSplits: 1).last {
                servidores.append(AnalisisIP.sinZona(v.trimmingCharacters(in: .whitespaces)))
            }
            else if s.hasPrefix("search domain["), let v = s.split(separator: ":", maxSplits: 1).last { busqueda.append(v.trimmingCharacters(in: .whitespaces)) }
            else if let v = valor(s, "if_index"), let a = v.firstIndex(of: "("), let b = v.firstIndex(of: ")") {
                interfaz = String(v[v.index(after: a)..<b])
            }
        }
        cerrar()
        return res
    }

    private static func valor(_ linea: String, _ clave: String) -> String? {
        guard linea.hasPrefix(clave) else { return nil }
        let resto = linea.dropFirst(clave.count).trimmingCharacters(in: .whitespaces)
        guard resto.hasPrefix(":") else { return nil }
        return resto.dropFirst().trimmingCharacters(in: .whitespaces)
    }

    struct ServicioVPN: Equatable { let estado: String; let nombre: String; let tipo: String }

    /// `scutil --nc list`: «* (Connected)   UUID VPN (io.tailscale.ipn.macsys) "Tailscale"   [VPN/io.tailscale.ipn.macsys]».
    static func serviciosNC(_ texto: String) -> [ServicioVPN] {
        var r: [ServicioVPN] = []
        for l in texto.split(separator: "\n") {
            let s = String(l)
            guard let a = s.firstIndex(of: "("), let b = s[a...].firstIndex(of: ")") else { continue }
            let estado = String(s[s.index(after: a)..<b])
            guard ["Connected", "Disconnected", "Connecting", "Disconnecting", "Invalid"].contains(estado) else { continue }
            var nombre = ""
            if let q1 = s.firstIndex(of: "\""), let q2 = s[s.index(after: q1)...].firstIndex(of: "\"") {
                nombre = String(s[s.index(after: q1)..<q2])
            }
            var tipo = ""
            if let c1 = s.lastIndex(of: "["), let c2 = s.lastIndex(of: "]"), c1 < c2 { tipo = String(s[s.index(after: c1)..<c2]) }
            r.append(ServicioVPN(estado: estado, nombre: nombre, tipo: tipo))
        }
        return r
    }

    struct ExtensionSistema: Equatable { let categoria: String; let bundle: String; let nombre: String; let estado: String
        var activa: Bool { estado.contains("activated enabled") }
        var esRed: Bool { categoria.contains("network_extension") }
    }

    /// `systemextensionsctl list` (columnas separadas por tabuladores).
    static func extensiones(_ texto: String) -> [ExtensionSistema] {
        var r: [ExtensionSistema] = []; var categoria = ""
        for l in texto.split(separator: "\n") {
            let s = String(l)
            if s.hasPrefix("---") {
                categoria = s.split(separator: " ").dropFirst().first.map(String.init) ?? ""; continue
            }
            let c = s.components(separatedBy: "\t")
            guard c.count >= 6, c[3].contains(".") else { continue }
            let bundle = String(c[3].split(separator: " ").first ?? "")
            let estado = c[5].trimmingCharacters(in: CharacterSet(charactersIn: "[] "))
            r.append(ExtensionSistema(categoria: categoria, bundle: bundle, nombre: c[4], estado: estado))
        }
        return r
    }

    /// Nombres de procesos (último componente de la ruta de `ps -axo comm=`).
    static func procesos(_ texto: String) -> Set<String> {
        var r = Set<String>()
        for l in texto.split(separator: "\n") {
            let s = l.trimmingCharacters(in: .whitespaces)
            guard !s.isEmpty else { continue }
            if let n = s.split(separator: "/").last { r.insert(String(n)) }
        }
        return r
    }

    // MARK: - Firewall de macOS (compartido con SeguridadMac)

    enum Firewall {
        struct App: Equatable { let ruta: String; let permitir: Bool? }

        /// nil = no se sabe.
        static func encendido(_ global: String) -> Bool? {
            let g = global.lowercased()
            if g.contains("state = 0") || g.contains("disabled") { return false }
            if g.contains("state = 1") || g.contains("state = 2") || g.contains("enabled") { return true }
            return nil
        }
        /// «Bloquear todas las conexiones entrantes» (State = 2 o --getblockall enabled).
        static func bloqueaTodo(global: String, bloqueoTotal: String) -> Bool {
            if global.contains("State = 2") { return true }
            let b = bloqueoTotal.lowercased()
            return b.contains("block all") && (b.contains("enabled") || b.contains(" on"))
                && !b.contains("disabled")
        }
        static func sigilo(_ texto: String) -> Bool? {
            let t = texto.lowercased()
            if t.contains("stealth mode is on") || t.contains("stealth mode enabled") { return true }
            if t.contains("stealth mode is off") || t.contains("stealth mode disabled") { return false }
            return nil
        }
        /// `--listapps`: «12 : /ruta/App.app» y en la línea siguiente «(Allow incoming connections)».
        static func apps(_ texto: String) -> [App] {
            var r: [App] = []; var pendiente: String?
            func cerrar(_ p: Bool?) { if let x = pendiente { r.append(App(ruta: x, permitir: p)) }; pendiente = nil }
            for l in texto.split(separator: "\n") {
                let s = l.trimmingCharacters(in: .whitespaces)
                if let dos = s.range(of: " : "), Int(s[..<dos.lowerBound].trimmingCharacters(in: .whitespaces)) != nil {
                    cerrar(nil)
                    pendiente = s[dos.upperBound...].trimmingCharacters(in: .whitespaces)
                } else if pendiente != nil {
                    if s.localizedCaseInsensitiveContains("allow") { cerrar(true) }
                    else if s.localizedCaseInsensitiveContains("block") { cerrar(false) }
                }
            }
            cerrar(nil)
            return r
        }
        /// El paquete .app que contiene el ejecutable (lo que se elige con «+» en Ajustes).
        static func paquete(_ ejecutable: String) -> String {
            if let r = ejecutable.range(of: ".app/") { return String(ejecutable[..<r.lowerBound]) + ".app" }
            return ejecutable
        }
        /// La entrada de la lista que corresponde a ese ejecutable (misma ruta o su .app).
        static func entrada(_ apps: [App], ejecutable: String) -> App? {
            apps.first { $0.ruta == ejecutable || ejecutable.hasPrefix($0.ruta.hasSuffix("/") ? $0.ruta : $0.ruta + "/") }
        }
        static func entradaPorNombre(_ apps: [App], _ nombre: String) -> App? {
            apps.first { ($0.ruta as NSString).lastPathComponent == nombre }
        }
    }

    // MARK: - Apps conocidas

    struct Conocida { let id: String; let nombre: String; let prefijos: [String]; let procesos: [String] }

    /// VPN «genéricas» (NordVPN, Tailscale, Little Snitch y LuLu tienen detectores propios).
    /// Prefijos de su documentación pública / salidas reales de systemextensionsctl.
    static let vpnsConocidas: [Conocida] = [
        Conocida(id: "proton", nombre: "Proton VPN", prefijos: ["ch.protonvpn."], procesos: ["Proton VPN"]),
        Conocida(id: "wireguard", nombre: "WireGuard", prefijos: ["com.wireguard."], procesos: ["WireGuard"]),
        Conocida(id: "mullvad", nombre: "Mullvad VPN", prefijos: ["net.mullvad."], procesos: ["Mullvad VPN", "mullvad-daemon"]),
        Conocida(id: "warp", nombre: "Cloudflare WARP (1.1.1.1)", prefijos: ["com.cloudflare.1dot1dot1dot1"], procesos: ["Cloudflare WARP", "CloudflareWARP"]),
        Conocida(id: "expressvpn", nombre: "ExpressVPN", prefijos: ["com.expressvpn."], procesos: ["ExpressVPN"]),
        Conocida(id: "surfshark", nombre: "Surfshark", prefijos: ["com.surfshark."], procesos: ["Surfshark"]),
        Conocida(id: "cisco", nombre: "Cisco Secure Client (AnyConnect)", prefijos: ["com.cisco.anyconnect", "com.cisco.secureclient"], procesos: ["Cisco Secure Client", "Cisco AnyConnect Secure Mobility Client", "vpnagentd"]),
        Conocida(id: "globalprotect", nombre: "GlobalProtect", prefijos: ["com.paloaltonetworks."], procesos: ["GlobalProtect", "PanGPS"]),
        Conocida(id: "forticlient", nombre: "FortiClient", prefijos: ["com.fortinet."], procesos: ["FortiClient", "FortiClientAgent"]),
    ]
    static let prefijosEspeciales = ["com.nordvpn.", "io.tailscale.", "at.obdev.littlesnitch", "com.objective-see.lulu", "com.apple."]

    /// DNS públicos de NordVPN (support.nordvpn.com, «NordVPN DNS server addresses»). Heurístico.
    static let dnsNordVPN: Set<String> = ["103.86.96.100", "103.86.99.100"]
    /// MagicDNS de Tailscale (tailscale.com/kb/1081/magicdns).
    static let dnsTailscale = "100.100.100.100"

    // MARK: - Análisis (puro)

    static func analizar(_ m: Muestras, appEjecutable: String) -> [Hallazgo] {
        var h: [Hallazgo] = []
        let ifs = interfaces(m.ifconfig)
        let tuneles = ifs.filter { esTunel($0.nombre) && $0.arriba && (!$0.ipv4.isEmpty || !$0.ipv6.isEmpty) }
        let porDefecto = tunelesConRutaPorDefecto(m.netstatRutas)
        let res = resolvers(m.scutilDNS)
        let todosDNS = Set(res.flatMap { $0.servidores })
        let nc = serviciosNC(m.scutilNC)
        let conectados = nc.filter { $0.estado == "Connected" }
        let exts = extensiones(m.sistemaExt)
        let activas = exts.filter { $0.activa }
        let procs = procesos(m.procesos)
        let cgnat = tuneles.filter { $0.ipv4.contains(where: AnalisisIP.esCGNAT) }
        func ext(_ prefijo: String) -> [ExtensionSistema] { activas.filter { $0.bundle.hasPrefix(prefijo) } }
        func corre(_ nombres: [String]) -> Bool { nombres.contains { procs.contains($0) } }
        func describir(_ t: [Interfaz]) -> String { t.map { "\($0.nombre) \(($0.ipv4 + $0.ipv6).joined(separator: ", "))" }.joined(separator: "; ") }

        let nordAbierto = corre(["NordVPN"])
        let nordShield = !ext("com.nordvpn.macos.Shield").isEmpty
        let tailscale = corre(["Tailscale", "tailscaled", "IPNExtension"]) || !ext("io.tailscale.").isEmpty
        let magicDNS = todosDNS.contains(dnsTailscale)

        // ¿Quién es la VPN conectada?
        func nombreDeServicio(_ s: ServicioVPN) -> String {
            let t = s.tipo.lowercased()
            if t.contains("com.nordvpn") { return "NordVPN" }
            if t.contains("io.tailscale") { return "Tailscale" }
            if let c = vpnsConocidas.first(where: { c in c.prefijos.contains { t.contains($0.lowercased()) } }) { return c.nombre }
            return s.nombre.isEmpty ? "una VPN" : "«\(s.nombre)»"
        }
        var vpnPorDefecto: String?
        if !porDefecto.isEmpty {
            if let s = conectados.first { vpnPorDefecto = nombreDeServicio(s) }
            else if nordAbierto && !(tailscale && porDefecto.allSatisfy { n in cgnat.contains { $0.nombre == n } }) { vpnPorDefecto = "NordVPN" }
            else if tailscale && porDefecto.contains(where: { n in cgnat.contains { $0.nombre == n } }) { vpnPorDefecto = "Tailscale (nodo de salida)" }
            else if let c = vpnsConocidas.first(where: { corre($0.procesos) }) { vpnPorDefecto = c.nombre }
        }
        var vpnsYaDichas = Set<String>()

        // 1. VPN con la ruta por defecto: todo sale por el túnel.
        if !porDefecto.isEmpty {
            let quien = vpnPorDefecto ?? "Una VPN"
            vpnsYaDichas.insert(quien)
            let guia: String
            if quien == "NordVPN" {
                guia = "NordVPN está conectado: desconéctalo durante el evento (o pausa la VPN). Si usas Meshnet o Threat Protection, apágalos también: cambian el DNS de la Mac."
            } else if quien.hasPrefix("Tailscale") {
                guia = "Tailscale manda todo el tráfico por otro equipo («nodo de salida»). Durante el evento, en el icono de Tailscale de la barra de menú elige «Exit node › None» o desconéctalo."
            } else {
                guia = "\(quien == "Una VPN" ? "Una VPN" : quien) manda todo el tráfico de esta Mac por un túnel: los teléfonos pueden no llegar al nodo y el DNS cambia. Desconéctala durante el evento (desde su icono en la barra de menú o en Ajustes del Sistema › VPN)."
            }
            let t = tuneles.filter { porDefecto.contains($0.nombre) }
            h.append(Hallazgo(id: "vpn-conectada", nivel: .problema,
                              titulo: quien == "Una VPN" ? "Hay una VPN conectada" : "\(quien) está conectado",
                              guia: guia,
                              detalle: "Ruta por defecto por \(porDefecto.joined(separator: ", "))" + (t.isEmpty ? "" : " (\(describir(t)))") +
                                (conectados.isEmpty ? "" : ". scutil --nc: " + conectados.map { "\($0.nombre) [\($0.tipo)]" }.joined(separator: ", "))))
        } else {
            // 2. Servicios VPN conectados sin ruta por defecto (túnel parcial).
            for (i, s) in conectados.enumerated() {
                let quien = nombreDeServicio(s)
                if quien == "Tailscale" && tailscale { continue }   // lo dice el detector de Tailscale
                vpnsYaDichas.insert(quien)
                h.append(Hallazgo(id: "vpn-servicio-\(i)", nivel: .aviso,
                                  titulo: quien == "NordVPN" ? "NordVPN está conectado" : "Otra VPN conectada: \(quien)",
                                  guia: quien == "NordVPN"
                                    ? "NordVPN está conectado: desconéctalo durante el evento (o pausa la VPN). Si usas Meshnet o Threat Protection, apágalos también: cambian el DNS de la Mac."
                                    : "Desconéctala durante el evento (desde su icono en la barra de menú o en Ajustes del Sistema › VPN). Una VPN puede cambiar el DNS de la Mac o esconderla de los teléfonos.",
                                  detalle: "scutil --nc list: (\(s.estado)) \"\(s.nombre)\" [\(s.tipo)]"))
            }
        }

        // 3. NordVPN: Meshnet, abierto, protección, DNS.
        let meshnet = nordAbierto && !cgnat.isEmpty && porDefecto.isEmpty && (!tailscale || cgnat.count >= 2)
        if meshnet {
            h.append(Hallazgo(id: "nordvpn-meshnet", nivel: .aviso, titulo: "Meshnet de NordVPN activo",
                              guia: "Meshnet crea una red privada con otros equipos y puede cambiar el DNS de la Mac. Apágalo durante el evento: NordVPN › Meshnet › desactivar.",
                              detalle: "Túnel con dirección 100.64.0.0/10 sin ruta por defecto: \(describir(cgnat)) (heurístico)"))
        }
        if nordAbierto && vpnPorDefecto != "NordVPN" && !vpnsYaDichas.contains("NordVPN") && !meshnet {
            h.append(Hallazgo(id: "nordvpn-abierto", nivel: .info, titulo: "NordVPN está abierto (desconectado)",
                              guia: "Está bien así. No lo conectes durante el evento: con la VPN conectada los teléfonos pueden dejar de ver el nodo.",
                              detalle: "Proceso NordVPN en marcha; sin ruta por defecto por un túnel."))
        }
        if nordShield {
            h.append(Hallazgo(id: "nordvpn-proteccion", nivel: .aviso, titulo: "La protección de NordVPN está activa",
                              guia: "La protección de NordVPN (Threat Protection) revisa las conexiones y el DNS de esta Mac y puede bloquear a los teléfonos o cambiar las respuestas del DNS. Durante el evento apágala en NordVPN › Threat Protection, o cierra NordVPN.",
                              detalle: "Extensión de sistema com.nordvpn.macos.Shield [activated enabled]."))
        }
        let nordDNS = todosDNS.intersection(dnsNordVPN)
        if !nordDNS.isEmpty {
            h.append(Hallazgo(id: "nordvpn-dns", nivel: .aviso, titulo: "La Mac usa el DNS de NordVPN",
                              guia: "NordVPN cambió el DNS de esta Mac. Desconecta NordVPN durante el evento y apaga «DNS personalizado» en sus ajustes si lo usas.",
                              detalle: "Servidores DNS \(nordDNS.sorted().joined(separator: ", ")) (direcciones públicas de NordVPN; heurístico)."))
        }

        // 4. Tailscale.
        if tailscale && vpnPorDefecto?.hasPrefix("Tailscale") != true {
            let conectado = !cgnat.isEmpty && !meshnet || magicDNS || conectados.contains { $0.tipo.contains("io.tailscale") }
            if conectado {
                h.append(Hallazgo(id: "tailscale", nivel: .aviso, titulo: "Tailscale está conectado",
                                  guia: "Desconéctalo durante el evento (icono de Tailscale en la barra de menú › Disconnect). Su «MagicDNS» cambia el DNS de esta Mac.",
                                  detalle: (cgnat.isEmpty ? "" : "Túnel: \(describir(cgnat)). ") + (magicDNS ? "DNS 100.100.100.100 (MagicDNS)." : "")))
            } else {
                h.append(Hallazgo(id: "tailscale", nivel: .info, titulo: "Tailscale instalado (desconectado)",
                                  guia: "Está bien así. No lo conectes durante el evento.",
                                  detalle: "Proceso o extensión de Tailscale presente, sin túnel 100.x."))
            }
        }

        // 5. DNS por un túnel (sin VPN con ruta por defecto: p. ej. DNS «dividido» de una VPN).
        if porDefecto.isEmpty {
            let porTunel = res.filter { $0.seccion == 0 && ($0.interfaz.map(esTunel) ?? false) && !$0.servidores.contains(dnsTailscale) }
            if !porTunel.isEmpty {
                let doms = porTunel.compactMap { $0.dominio }
                h.append(Hallazgo(id: "vpn-dns", nivel: .aviso, titulo: "Una VPN cambia el DNS de la Mac",
                                  guia: "Una VPN añadió su propio DNS\(doms.isEmpty ? "" : " para algunos dominios"). Desconéctala durante el evento.",
                                  detalle: porTunel.map { "resolver #\($0.numero) \($0.interfaz ?? "") \($0.dominio ?? "(todos)") → \($0.servidores.joined(separator: ", "))" }.joined(separator: "; ")))
            }
        }

        // 6. Otras VPN conocidas (instaladas o abiertas, sin conexión detectada).
        for c in vpnsConocidas where !vpnsYaDichas.contains(c.nombre) {
            let e = activas.filter { x in c.prefijos.contains { x.bundle.hasPrefix($0) } }
            let p = corre(c.procesos)
            guard !e.isEmpty || p else { continue }
            h.append(Hallazgo(id: "vpn-\(c.id)", nivel: .info, titulo: "\(c.nombre) instalado (sin conexión detectada)",
                              guia: "Está bien así. No lo conectes durante el evento: con la VPN conectada los teléfonos pueden dejar de ver el nodo.",
                              detalle: (e.map { "\($0.bundle) «\($0.nombre)» [\($0.estado)]" } + (p ? ["proceso en marcha"] : [])).joined(separator: "; ")))
        }

        // 7. Little Snitch y LuLu.
        let ls = ext("at.obdev.littlesnitch")
        let lsProc = procs.contains { $0.hasPrefix("Little Snitch") || $0.hasPrefix("at.obdev.littlesnitch") }
        if !ls.isEmpty || lsProc {
            h.append(Hallazgo(id: "littlesnitch", nivel: .aviso, titulo: "Little Snitch está activo",
                              guia: "Little Snitch puede bloquear a los teléfonos: en Little Snitch permite conexiones ENTRANTES a “Nodo Camporee” (puertos 80 y 443, y 53 si usas “Mi red”) y a limactl, o ponlo en modo silencioso “Permitir” durante el evento.",
                              detalle: (ls.map { "\($0.bundle) [\($0.estado)]" } + (lsProc ? ["procesos de Little Snitch en marcha"] : [])).joined(separator: "; ")))
        }
        let lulu = ext("com.objective-see.lulu")
        if !lulu.isEmpty || procs.contains("LuLu") {
            h.append(Hallazgo(id: "lulu", nivel: .aviso, titulo: "LuLu está activo",
                              guia: "LuLu puede bloquear al nodo. En LuLu › Reglas permite a “Nodo Camporee” y a limactl, o desactiva LuLu durante el evento (icono de LuLu › Desactivar).",
                              detalle: (lulu.map { "\($0.bundle) [\($0.estado)]" }).joined(separator: "; ")))
        }

        // 8. Otros filtros / proxies / VPN de red desconocidos.
        let conocidos = prefijosEspeciales + vpnsConocidas.flatMap { $0.prefijos }
        for e in activas where e.esRed && !conocidos.contains(where: { e.bundle.hasPrefix($0) }) {
            h.append(Hallazgo(id: "filtro-\(e.bundle)", nivel: .aviso, titulo: "Filtro de red de otra app: \(e.nombre)",
                              guia: "Esta app revisa o desvía las conexiones de la Mac y podría bloquear a los teléfonos o cambiar el DNS. Si el nodo falla, desactívala durante el evento (desde la propia app, o en Ajustes del Sistema › General › Ítems de inicio y extensiones › Extensiones de red).",
                              detalle: "\(e.bundle) [\(e.estado)]"))
        }

        // 9. Firewall de macOS.
        h += firewall(m, appEjecutable: appEjecutable, procs: m.procesos)

        // 10. DNS que no es el router.
        h += dnsNoRouter(m, ifs: ifs, res: res)

        // 11. Relay privado de iCloud: no hay forma oficial de verlo desde la línea de órdenes.
        h.append(Hallazgo(id: "relay-privado", nivel: .info, titulo: "Relay privado de iCloud (no se puede comprobar automáticamente)",
                          guia: "Si tienes Relay privado de iCloud, desactívalo para la red del evento (Ajustes › Wi-Fi › (red) › Limitar seguimiento de IP / Relay privado).",
                          detalle: "macOS no ofrece una orden documentada para leer este ajuste."))

        if !h.contains(where: { $0.nivel == .aviso || $0.nivel == .problema }) {
            h.append(Hallazgo(id: "sin-interferencias", nivel: .ok, titulo: "Sin interferencias",
                              guia: "No se encontró ninguna VPN, filtro de red ni bloqueo del firewall que estorbe al nodo.", detalle: ""))
        }
        return ordenar(h)
    }

    static func ordenar(_ h: [Hallazgo]) -> [Hallazgo] {
        func peso(_ n: Nivel) -> Int { switch n { case .problema: return 0; case .aviso: return 1; case .info: return 2; case .ok: return 3 } }
        return h.enumerated().sorted { (peso($0.element.nivel), $0.offset) < (peso($1.element.nivel), $1.offset) }.map { $0.element }
    }

    static func firewall(_ m: Muestras, appEjecutable: String, procs: String) -> [Hallazgo] {
        guard let encendido = Firewall.encendido(m.firewallGlobal) else { return [] }
        let pasos = "Ajustes del Sistema › Red › Firewall › Opciones…"
        if !encendido {
            return [Hallazgo(id: "firewall-apagado", nivel: .info, titulo: "El firewall de macOS está apagado",
                             guia: "No estorba a los teléfonos. (La revisión de seguridad recomienda encenderlo y permitir sólo a Nodo Camporee.)",
                             detalle: m.firewallGlobal.trimmingCharacters(in: .whitespacesAndNewlines))]
        }
        if Firewall.bloqueaTodo(global: m.firewallGlobal, bloqueoTotal: m.firewallBloqueoTotal) {
            return [Hallazgo(id: "firewall-bloquea-todo", nivel: .problema, titulo: "El firewall bloquea todas las conexiones entrantes",
                             guia: "Ningún teléfono puede llegar al nodo. \(pasos) › apaga «Bloquear todas las conexiones entrantes» (deja el firewall encendido).",
                             detalle: (m.firewallGlobal + " " + m.firewallBloqueoTotal).trimmingCharacters(in: .whitespacesAndNewlines))]
        }
        var h: [Hallazgo] = []
        let apps = Firewall.apps(m.firewallApps)
        let paquete = Firewall.paquete(appEjecutable)
        if let e = Firewall.entrada(apps, ejecutable: appEjecutable) {
            if e.permitir == false {
                h.append(Hallazgo(id: "firewall-app", nivel: .problema, titulo: "El firewall bloquea a Nodo Camporee",
                                  guia: "\(pasos) › busca «Nodo Camporee» y cambia a «Permitir conexiones entrantes».",
                                  detalle: "Bloqueada: \(e.ruta)"))
            }
        } else {
            let otra = Firewall.entradaPorNombre(apps, (paquete as NSString).lastPathComponent)
            h.append(Hallazgo(id: "firewall-app", nivel: .aviso, titulo: "Falta permitir a Nodo Camporee en el firewall",
                              guia: "\(pasos) › pulsa «+», elige \(paquete) y pon «Permitir conexiones entrantes». (Si al encender aparece «¿Permitir conexiones entrantes?», pulsa Permitir.)",
                              detalle: "No está en la lista: \(appEjecutable)" + (otra.map { ". Hay otra copia en la lista: \($0.ruta)" } ?? "")))
        }
        if let e = Firewall.entradaPorNombre(apps, "limactl") {
            if e.permitir == false {
                h.append(Hallazgo(id: "firewall-limactl", nivel: .problema, titulo: "El firewall bloquea a limactl",
                                  guia: "\(pasos) › busca «limactl» y cambia a «Permitir conexiones entrantes».", detalle: "Bloqueada: \(e.ruta)"))
            }
        } else {
            let ruta = m.limactlRuta ?? procs.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.first { $0.hasSuffix("/limactl") }
            h.append(Hallazgo(id: "firewall-limactl", nivel: .aviso, titulo: "Falta permitir a limactl en el firewall",
                              guia: "\(pasos) › pulsa «+», elige \(ruta ?? "limactl (en /opt/homebrew/bin)") y pon «Permitir conexiones entrantes». (En el selector pulsa Cmd+Mayús+G para escribir la ruta.)",
                              detalle: "limactl no está en la lista del firewall" + (ruta.map { "; ruta: \($0)" } ?? "")))
        }
        return h
    }

    static func ipsEnTexto(_ t: String) -> [String] {
        t.split(whereSeparator: { $0 == "\n" || $0 == " " || $0 == "\t" || $0 == "," }).map(String.init)
            .filter { AnalisisIP.esIPv4($0) || ($0.contains(":") && $0.allSatisfy { $0.isHexDigit || $0 == ":" || $0 == "." }) }
    }

    static func dnsNoRouter(_ m: Muestras, ifs: [Interfaz], res: [Resolver]) -> [Hallazgo] {
        guard let gw = m.gatewayIP, !gw.isEmpty else { return [] }
        guard let primero = res.first(where: { $0.seccion == 0 && $0.dominio == nil && !$0.servidores.isEmpty }) else { return [] }
        if let i = primero.interfaz, esTunel(i) { return [] }   // ya lo dice el detector de VPN
        var esperados = Set([gw] + m.resolversRouter + ["127.0.0.1", "::1"])
        for i in ifs where !esTunel(i.nombre) { esperados.formUnion(i.ipv4); esperados.formUnion(i.ipv6) }
        let otros = primero.servidores.filter { !esperados.contains($0) && !AnalisisIP.esLinkLocalV6($0) && !AnalisisIP.esLoopback($0) }
        guard !otros.isEmpty else { return [] }
        let manual = Set(ipsEnTexto(m.dnsManual))
        let lista = otros.joined(separator: ", ")
        if !manual.isDisjoint(with: otros) {
            return [Hallazgo(id: "dns-manual", nivel: .aviso, titulo: "La Mac tiene DNS escritos a mano: \(lista)",
                             guia: "Durante el evento la Mac debe usar el DNS del router. Ajustes del Sistema › Red › Wi-Fi (o Ethernet) › Detalles… › DNS: quita \(lista) con «−» y pulsa OK.",
                             detalle: "networksetup -getdnsservers: \(manual.sorted().joined(separator: ", ")); router \(gw)")]
        }
        let v6 = otros.contains { $0.contains(":") }
        return [Hallazgo(id: "dns-no-router", nivel: .aviso, titulo: "La Mac pregunta el DNS a \(lista), no al router",
                         guia: v6
                            ? "El router (o el proveedor de internet) reparte también servidores DNS por IPv6. Los teléfonos podrían saltarse el DNS del evento. En la red del evento usa el GL.iNet (la app lo configura) o desactiva IPv6 en el router durante el evento."
                            : "Otra app o el router le dan a la Mac un DNS que no es el del router. Revisa Ajustes del Sistema › Red › Wi-Fi › Detalles… › DNS y desconecta cualquier VPN o app de DNS durante el evento.",
                         detalle: "resolver #1 (\(primero.interfaz ?? "?")): \(primero.servidores.joined(separator: ", ")); router \(gw)")]
    }

    // MARK: - Recolectar (lee esta Mac; sólo lectura)

    static func muestrasDeEstaMac() -> Muestras {
        let fw = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        var m = Muestras()
        m.ifconfig = Comando.correr("/sbin/ifconfig", [])
        m.netstatRutas = Comando.correr("/usr/sbin/netstat", ["-rn", "-f", "inet"])
        m.scutilDNS = Comando.correr("/usr/sbin/scutil", ["--dns"])
        m.scutilNC = Comando.correr("/usr/sbin/scutil", ["--nc", "list"])
        m.sistemaExt = Comando.correr("/usr/bin/systemextensionsctl", ["list"])
        m.procesos = Comando.correr("/bin/ps", ["-axo", "comm="])
        m.firewallGlobal = Comando.correr(fw, ["--getglobalstate"])
        m.firewallBloqueoTotal = Comando.correr(fw, ["--getblockall"])
        m.firewallApps = Comando.correr(fw, ["--listapps"])
        m.firewallSigilo = Comando.correr(fw, ["--getstealthmode"])
        let ruta = Comando.correr("/sbin/route", ["-n", "get", "default"])
        for l in ruta.split(separator: "\n") {
            let s = l.trimmingCharacters(in: .whitespaces)
            if s.hasPrefix("gateway:") { m.gatewayIP = s.dropFirst("gateway:".count).trimmingCharacters(in: .whitespaces) }
        }
        var manual = ""
        for s in Comando.correr("/usr/sbin/networksetup", ["-listallnetworkservices"]).split(separator: "\n").dropFirst()
        where !s.hasPrefix("*") {
            manual += Comando.correr("/usr/sbin/networksetup", ["-getdnsservers", String(s)]) + "\n"
        }
        m.dnsManual = manual
        for c in ["/opt/homebrew/bin/limactl", "/usr/local/bin/limactl"] where FileManager.default.fileExists(atPath: c) {
            m.limactlRuta = (try? FileManager.default.destinationOfSymbolicLink(atPath: c)).map {
                $0.hasPrefix("/") ? $0 : ((c as NSString).deletingLastPathComponent as NSString).appendingPathComponent($0)
            }.map { ($0 as NSString).standardizingPath } ?? c
            break
        }
        return m
    }

    static func recolectar(appEjecutable: String) -> [Hallazgo] {
        analizar(muestrasDeEstaMac(), appEjecutable: appEjecutable)
    }
}
