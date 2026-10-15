// Pruebas de los analizadores (app/Analisis.swift, Interferencias.swift, SeguridadMac.swift,
// Dispositivos.swift) con las salidas de pruebas/muestras. Lo corre pruebas/probar-analizadores.sh.
//   main <dir-muestras>           → pruebas (sale con 1 si alguna falla)
//   main <dir-muestras> --real    → además corre `recolectar` en esta Mac y lista los títulos
import Foundation

let args = CommandLine.arguments
let dir = args.count > 1 ? args[1] : "pruebas/muestras"
var pasadas = 0, fallidas = 0

func check(_ cond: Bool, _ nombre: String, _ extra: @autoclosure () -> String = "") {
    if cond { pasadas += 1 } else { fallidas += 1; print("✗ \(nombre)\(extra().isEmpty ? "" : " — \(extra())")") }
}
func leer(_ grupo: String, _ archivo: String) -> String? {
    try? String(contentsOfFile: "\(dir)/\(grupo)/\(archivo)", encoding: .utf8)
}
/// Lee del grupo; si el archivo no existe ahí, de `base`.
func leerCon(_ grupo: String, _ archivo: String, base: String) -> String {
    leer(grupo, archivo) ?? leer(base, archivo) ?? ""
}
func ids(_ h: [Hallazgo]) -> [String] { h.map { $0.id } }
func uno(_ h: [Hallazgo], _ id: String) -> Hallazgo? { h.first { $0.id == id } }

let APP = "/Users/USUARIO/Applications/Nodo Camporee.app/Contents/MacOS/Nodo Camporee"

func interf(_ grupo: String, base: String = "limpia", gateway: String? = "192.168.8.1", router: [String] = []) -> Interferencias.Muestras {
    func f(_ a: String) -> String { leerCon(grupo, a, base: base) }
    return Interferencias.Muestras(ifconfig: f("ifconfig.txt"), netstatRutas: f("netstat-rn-inet.txt"), scutilDNS: f("scutil-dns.txt"),
                                   scutilNC: f("scutil-nc-list.txt"), sistemaExt: f("systemextensionsctl-list.txt"), procesos: f("ps-axo-comm.txt"),
                                   firewallGlobal: f("fw-getglobalstate.txt"), firewallApps: f("fw-listapps.txt"), firewallSigilo: f("fw-getstealthmode.txt"),
                                   gatewayIP: gateway, resolversRouter: router, firewallBloqueoTotal: f("fw-getblockall.txt"),
                                   dnsManual: f("networksetup-getdnsservers.txt"))
}
func analizar(_ grupo: String, base: String = "limpia", gateway: String? = "192.168.8.1", router: [String] = [], app: String = APP) -> [Hallazgo] {
    Interferencias.analizar(interf(grupo, base: base, gateway: gateway, router: router), appEjecutable: app)
}

// MARK: - Interferencias: lectores
do {
    let ifs = Interferencias.interfaces(leer("limpia", "ifconfig.txt") ?? "")
    let u = ifs.first { $0.nombre == "utun0" }
    check(u?.arriba == true && u?.ipv4.isEmpty == true && u?.ipv6.isEmpty == true, "ifconfig: utun0 sin dirección global")
    check(ifs.first { $0.nombre == "en0" }?.ipv4 == ["192.168.8.20"], "ifconfig: IP de en0")
    check(Interferencias.tunelesConRutaPorDefecto(leer("nordvpn-conectado", "netstat-rn-inet.txt") ?? "") == ["utun4"], "rutas partidas 0/1 por utun4")
    check(Interferencias.tunelesConRutaPorDefecto(leer("limpia", "netstat-rn-inet.txt") ?? "").isEmpty, "rutas: sin túnel en limpia")
    let r = Interferencias.resolvers(leer("nordvpn-conectado", "scutil-dns.txt") ?? "")
    check(r.first?.interfaz == "utun4" && r.first?.servidores == ["103.86.96.100", "103.86.99.100"], "scutil --dns: resolver #1 por utun4")
    let rr = Interferencias.resolvers(leer("mac-real", "scutil-dns.txt") ?? "")
    check(rr.filter { $0.seccion == 1 }.count == 1 && rr.first?.servidores.count == 3, "scutil --dns real: secciones y servidores")
    let nc = Interferencias.serviciosNC(leer("vpn-otras", "scutil-nc-list.txt") ?? "")
    check(nc.count == 3 && nc[0].estado == "Connected" && nc[0].nombre == "oficina" && nc[0].tipo == "VPN/com.wireguard.macos", "scutil --nc list", "\(nc)")
    let ex = Interferencias.extensiones(leer("mac-real", "systemextensionsctl-list.txt") ?? "")
    check(ex.count == 5 && ex.filter { $0.activa }.count == 4, "systemextensionsctl real: 5 extensiones, 4 activas", "\(ex.count)")
    check(ex.contains { $0.bundle == "at.obdev.littlesnitch.networkextension" && $0.esRed && $0.activa }, "systemextensionsctl: Little Snitch de red")
    let apps = Interferencias.Firewall.apps(leer("mac-real", "fw-listapps.txt") ?? "")
    check(apps.count == 26 && apps.allSatisfy { $0.permitir == true }, "socketfilterfw --listapps real: 26 permitidas", "\(apps.count)")
    let bl = Interferencias.Firewall.apps(leer("firewall-app-bloqueada", "fw-listapps.txt") ?? "")
    check(bl.first?.permitir == false && bl.last?.permitir == true, "--listapps: Block/Allow")
    check(Interferencias.Firewall.encendido("Firewall is disabled. (State = 0)") == false && Interferencias.Firewall.encendido("Firewall is enabled. (State = 1)") == true
          && Interferencias.Firewall.encendido("") == nil, "firewall: estado global")
    check(Interferencias.Firewall.paquete(APP) == "/Users/USUARIO/Applications/Nodo Camporee.app", "firewall: paquete .app del ejecutable")
}

// MARK: - Interferencias: detectores
do {
    let h = analizar("limpia")
    check(uno(h, "sin-interferencias")?.nivel == .ok, "limpia: «Sin interferencias»", "\(ids(h))")
    check(!h.contains { $0.nivel == .aviso || $0.nivel == .problema }, "limpia: sin avisos", "\(ids(h))")
    check(uno(h, "relay-privado")?.nivel == .info, "limpia: pista de Relay privado (info)")

    let n = analizar("nordvpn-conectado")
    check(uno(n, "vpn-conectada")?.nivel == .problema && uno(n, "vpn-conectada")?.titulo == "NordVPN está conectado", "NordVPN conectado → problema", "\(ids(n))")
    check(uno(n, "vpn-conectada")?.guia.contains("Meshnet") == true, "NordVPN: guía menciona Meshnet/Threat Protection")
    check(n.first?.nivel == .problema, "orden: problema primero")
    check(uno(n, "nordvpn-proteccion")?.nivel == .aviso, "NordVPN: Shield activa → aviso")
    check(uno(n, "nordvpn-dns")?.nivel == .aviso, "NordVPN: DNS 103.86.x → aviso")
    check(uno(n, "nordvpn-abierto") == nil && uno(n, "sin-interferencias") == nil && uno(n, "vpn-dns") == nil, "NordVPN conectado: sin duplicados")

    let m = analizar("nordvpn-meshnet")
    check(uno(m, "nordvpn-meshnet")?.nivel == .aviso && uno(m, "vpn-conectada") == nil && uno(m, "nordvpn-abierto") == nil, "Meshnet de NordVPN", "\(ids(m))")

    let t = analizar("tailscale")
    check(uno(t, "tailscale")?.nivel == .aviso && uno(t, "tailscale")?.detalle.contains("MagicDNS") == true, "Tailscale conectado (MagicDNS)", "\(ids(t))")
    check(uno(t, "vpn-conectada") == nil && uno(t, "vpn-servicio-0") == nil && uno(t, "nordvpn-meshnet") == nil && uno(t, "vpn-dns") == nil, "Tailscale: sin duplicados", "\(ids(t))")

    let f = analizar("filtros")
    check(uno(f, "littlesnitch")?.nivel == .aviso, "Little Snitch activo → aviso", "\(ids(f))")
    check(uno(f, "littlesnitch")?.guia.contains("“Nodo Camporee” (puertos 80 y 443, y 53") == true, "Little Snitch: guía exacta")
    check(uno(f, "lulu")?.nivel == .aviso, "LuLu activo → aviso")
    check(uno(f, "filtro-com.ejemplo.filtro.proxy")?.nivel == .aviso, "filtro de red desconocido → aviso")
    check(!f.contains { $0.id.contains("nektony") }, "extensión «waiting for user» no cuenta")

    let o = analizar("vpn-otras")
    check(uno(o, "vpn-servicio-0")?.titulo.contains("WireGuard") == true, "WireGuard conectado (scutil) → aviso", "\(ids(o))")
    check(!o.contains { $0.titulo.contains("Cisco") }, "Cisco desconectado y sin extensión → nada")
    check(uno(o, "vpn-warp")?.nivel == .info, "WARP instalado → info")
    check(uno(o, "vpn-dns")?.nivel == .aviso && uno(o, "vpn-dns")?.detalle.contains("oficina.ejemplo") == true, "DNS suplementario por utun → aviso")

    check(uno(analizar("firewall-bloquea-todo"), "firewall-bloquea-todo")?.nivel == .problema, "firewall bloquea todo → problema")
    check(uno(analizar("firewall-app-bloqueada"), "firewall-app")?.nivel == .problema, "firewall bloquea la app → problema")
    check(uno(h, "firewall-app") == nil && uno(h, "firewall-limactl") == nil, "firewall con app y limactl permitidos → nada")
    let apagado = analizar("firewall-apagado")
    check(uno(apagado, "firewall-apagado")?.nivel == .info && uno(apagado, "sin-interferencias") != nil, "firewall apagado → info (y sin interferencias)")
    var ml = interf("mac-real", base: "mac-real", gateway: "192.168.100.1"); ml.limactlRuta = "/opt/homebrew/Cellar/lima/2.2.0/bin/limactl"
    check(uno(Interferencias.analizar(ml, appEjecutable: APP), "firewall-limactl")?.guia.contains("Cellar/lima/2.2.0") == true, "limactl: ruta real (sin enlace) preferida")
    let otra = analizar("limpia", app: "/Applications/Nodo Camporee.app/Contents/MacOS/Nodo Camporee")
    check(uno(otra, "firewall-app")?.nivel == .aviso && uno(otra, "firewall-app")?.guia.contains("/Applications/Nodo Camporee.app") == true
          && uno(otra, "firewall-app")?.detalle.contains("otra copia") == true, "app no permitida → aviso con ruta exacta")

    let real = analizar("mac-real", base: "mac-real", gateway: "192.168.100.1")
    check(uno(real, "firewall-limactl")?.guia.contains("/opt/homebrew/bin/limactl") == true, "limactl sin permitir → aviso con ruta (de ps)", uno(real, "firewall-limactl")?.guia ?? "nil")
    check(uno(real, "dns-no-router")?.nivel == .aviso && uno(real, "dns-no-router")?.titulo.contains("2001:4860:4860::8888") == true, "DNS por IPv6 que no es el router → aviso", "\(ids(real))")
    check(uno(real, "littlesnitch") != nil && uno(real, "nordvpn-proteccion") != nil && uno(real, "nordvpn-abierto")?.nivel == .info
          && uno(real, "vpn-proton")?.nivel == .info && uno(real, "vpn-conectada") == nil, "Mac real capturada: LS, NordVPN abierto/protección, Proton", "\(ids(real))")
    check(!real.contains { $0.id.contains("nektony") }, "Mac real: Nektony (esperando al usuario) no cuenta")

    let dm = analizar("dns-manual")
    check(uno(dm, "dns-manual")?.titulo.contains("1.1.1.1") == true && uno(dm, "dns-no-router") == nil, "DNS manual → aviso dns-manual", "\(ids(dm))")
    check(uno(analizar("dns-manual", router: ["1.1.1.1", "8.8.8.8"]), "dns-manual") == nil, "DNS que el router reparte → nada")
    check(uno(analizar("dns-manual", gateway: nil), "dns-manual") == nil, "sin gateway no se compara el DNS")

    let x = uno(n, "vpn-conectada")!
    let rt = try? JSONDecoder().decode(Hallazgo.self, from: JSONEncoder().encode(x))
    check(rt == x, "Hallazgo: Codable ida y vuelta")
}

// MARK: - Seguridad
func seg(_ grupo: String, base: String = "mac-real") -> SeguridadMac.Muestras {
    func f(_ a: String) -> String { leerCon(grupo, a, base: base) }
    return SeguridadMac.Muestras(fdesetup: f("fdesetup-status.txt"), firewallGlobal: f("fw-getglobalstate.txt"), firewallBloqueoTotal: f("fw-getblockall.txt"),
                                 firewallSigilo: f("fw-getstealthmode.txt"), firewallApps: f("fw-listapps.txt"), sharing: f("sharing-l.txt"),
                                 netstatEscucha: f("netstat-anv-tcp-listen.txt"), procesos: f("ps-axo-comm.txt"), mediaSharing: f("defaults-mediasharingd.txt"),
                                 cupsctl: f("cupsctl.txt"), loginwindow: f("defaults-loginwindow.txt"), bloqueoPantalla: f("sysadminctl-screenlock.txt"),
                                 salvapantallas: f("defaults-screensaver.txt"), airdrop: f("defaults-sharingd-discoverable.txt"),
                                 pantallaCompartida: f("launchctl-screensharing.txt"))
}
do {
    let esc = SeguridadMac.escuchas(leer("mac-real", "netstat-anv-tcp-listen.txt") ?? "")
    check(esc.contains { $0.puerto == 53 && $0.expuesta && $0.proceso == "limactl" }, "netstat: *.53 de limactl expuesto")
    check(esc.contains { $0.puerto == 5432 && !$0.expuesta }, "netstat: 127.0.0.1 no está expuesto")

    let r = SeguridadMac.analizar(seg("mac-real"), appEjecutable: APP)
    check(uno(r, "filevault")?.nivel == .ok, "real: FileVault encendido")
    check(uno(r, "firewall")?.nivel == .ok && uno(r, "firewall-sigilo")?.nivel == .info, "real: firewall encendido, sigilo apagado (info)")
    check(uno(r, "firewall-otras")?.nivel == .info && uno(r, "firewall-otras")?.titulo.contains("apps") == true, "real: otras apps permitidas → info")
    check(uno(r, "compartir-archivos")?.nivel == .ok && uno(r, "sesion-remota")?.nivel == .ok && uno(r, "compartir-pantalla")?.nivel == .ok, "real: compartir apagado")
    check(uno(r, "airdrop")?.nivel == .aviso, "real: AirDrop «Everyone» → aviso")
    check(uno(r, "bloqueo-pantalla")?.nivel == .ok && uno(r, "inicio-automatico")?.nivel == .ok, "real: bloqueo inmediato, sin inicio automático")
    check(uno(r, "receptor-airplay")?.nivel == .info, "real: ControlCenter en 5000/7000 → receptor AirPlay (info)")

    var mm = seg("seguridad-mala")
    mm.firewallGlobal = leer("firewall-apagado", "fw-getglobalstate.txt") ?? ""
    let m = SeguridadMac.analizar(mm, appEjecutable: APP)
    check(uno(m, "filevault")?.nivel == .problema, "FileVault apagado → problema")
    check(uno(m, "firewall")?.nivel == .aviso, "firewall apagado → aviso")
    check(uno(m, "compartir-archivos")?.nivel == .problema, "Compartir archivos con invitado → problema")
    check(uno(m, "compartir-pantalla")?.nivel == .aviso && uno(m, "sesion-remota")?.nivel == .aviso, "Compartir pantalla y SSH → aviso")
    check(uno(m, "compartir-multimedia")?.nivel == .aviso && uno(m, "compartir-impresoras")?.nivel == .aviso, "multimedia e impresoras → aviso")
    check(uno(m, "inicio-automatico")?.nivel == .problema && uno(m, "inicio-automatico")?.detalle.contains("USUARIO") == false, "inicio automático → problema, sin nombre de usuario")
    check(uno(m, "invitado")?.nivel == .info && uno(m, "receptor-airplay")?.nivel == .info, "invitado y receptor AirPlay → info")
    check(uno(m, "bloqueo-pantalla")?.nivel == .aviso && uno(m, "airdrop")?.nivel == .aviso, "pantalla sin contraseña y AirDrop abierto → aviso")
    check(m.first?.nivel == .problema, "seguridad: problemas primero")

    let a = SeguridadMac.analizar(seg("seguridad-ard"), appEjecutable: APP)
    check(uno(a, "gestion-remota")?.nivel == .aviso && uno(a, "compartir-pantalla") == nil, "ARD → «Gestión remota», sin duplicar pantalla", "\(ids(a))")
    check(uno(a, "bloqueo-pantalla")?.titulo.contains("15 min") == true, "bloqueo a 900 s → aviso 15 min")
    var s = seg("seguridad-ard"); s.bloqueoPantalla = ""
    check(SeguridadMac.bloqueo(s).nivel == .ok, "bloqueo: respaldo con askForPassword=1, delay 0 → ok")
    s.salvapantallas = "{\n    tokenRemovalAction = 0;\n}"
    check(SeguridadMac.bloqueo(s).nivel == .info, "bloqueo: sin datos → info honesta")
}

// MARK: - Dispositivos
let AHORA = Date(timeIntervalSince1970: 1_792_087_200)   // 2026-10-15 18:00:00 UTC
do {
    let c = Dispositivos.caddy(leer("dispositivos", "caddy-acceso.log") ?? "")
    check(c.count == 65, "Caddy: 65 líneas válidas (se ignoran consola y basura)", "\(c.count)")
    let sin = c.first { $0.ip == "192.168.8.160" }
    check(sin != nil && sin?.agente == nil, "Caddy: sin client_ip usa remote_ip; sin cabeceras")
    check(c.first { $0.ip == "192.168.8.170" }?.ts == Date(timeIntervalSince1970: 1_792_087_190.5), "Caddy: ts ISO-8601")
    check(c.first?.status == 200 && c.first?.host == "events.adventist.club" && c.first?.ruta == "/eventos/ntam/actividades", "Caddy: campos y ruta sin query")

    let arp = Dispositivos.arp(leer("dispositivos", "arp-an.txt") ?? "")
    check(arp.count == 3 && arp["192.168.8.1"] == "94:83:c4:01:02:03", "arp: 3 equipos, MAC normalizada", "\(arp)")
    let ow = Dispositivos.leasesOpenWrt(leer("dispositivos", "dhcp.leases") ?? "")
    check(ow.count == 4 && ow[1].nombre == nil && ow[0].nombre == "iPhone" && ow[0].expira != nil, "OpenWrt: concesiones")
    let mk = Dispositivos.leasesMikroTik(leer("dispositivos", "mikrotik-leases.txt") ?? "")
    check(mk.count == 3 && mk[0].mac == "00:16:32:ab:cd:ef" && mk[0].nombre == "Galaxy-A54" && mk[1].nombre == "iPhone de Luis" && mk[2].ip == "192.168.88.10",
          "MikroTik: concesiones (comillas, sin active-*)", "\(mk)")

    let api = Dispositivos.api(Data((leer("dispositivos", "api-dispositivos.json") ?? "").utf8))
    let a142 = api.first { $0.ip == "192.168.8.142" }
    check(api.count == 3 && a142?.fallidos10min == 10 && a142?.correosDistintos == 6 && a142?.sospechoso == true && a142?.bloqueadoHasta != nil, "API: contrato final", "\(api)")
    check(api.first { $0.ip == "192.168.8.180" }?.fallidos10min == 2 && api[0].cuenta?.email == "director@ejemplo.org" && api[0].ultimo != nil, "API: tolerante (texto, campos faltantes)")
    check(Dispositivos.api(Data("basura".utf8)).isEmpty && Dispositivos.api(Data(#"[{"ip":"10.0.0.5"}]"#.utf8)).count == 1, "API: basura → [], arreglo suelto → ok")

    let mo = Dispositivos.leerMotor(Data((leer("dispositivos", "motor-dispositivos.json") ?? "").utf8))
    check(mo.api.count == 3 && mo.arp.count == 3 && mo.miIP == "192.168.8.20" && mo.router == "192.168.8.1" && mo.rutaAcceso.hasSuffix("acceso.log"), "leerMotor")

    let d = Dispositivos.agregar(caddy: c, arp: arp, leases: ow, api: api, bloqueados: ["DA:A1:19:AA:BB:CC"], ahora: AHORA)
    func dev(_ ip: String) -> Dispositivo? { d.first { $0.ip == ip } }
    check(d.first?.ip == "192.168.8.142", "orden: más alertas primero", d.map { $0.ip }.joined(separator: " "))
    check(dev("127.0.0.1") == nil, "loopback y /nodo-salud fuera")
    let s131 = dev("192.168.8.131")
    check(s131?.alertas.contains { $0.tipo == .escaneo } == true && s131?.rutasDistintas404 == 30, "escáner → alerta «escaneo»")
    check(s131?.macPrivada == true && s131?.fabricante == nil && s131?.bloqueado == true, "MAC privada sin fabricante; bloqueado por MAC")
    let p = dev("192.168.8.142")
    check(p?.alertas.map { $0.tipo } == [.muchosFallidos, .muchasCuentas] && p?.sinSesion == true, "probador de contraseñas → fallidos + cuentas", "\(p?.alertas ?? [])")
    check(p?.frenadoHasta != nil && p?.notas.first?.contains("frenadas") == true, "bloqueado_hasta → nota «cuentas frenadas»")
    let i = dev("192.168.8.120")
    check(i?.alertas.isEmpty == true && i?.cuenta?.nombre == "Director Ejemplo" && i?.fabricante == "Apple" && i?.nombre == "iPhone" && i?.peticiones10min == 21,
          "teléfono normal: sin alertas, cuenta, Apple, nombre", "\(String(describing: i))")
    let v = dev("192.168.8.150")
    check(v?.peticiones10min == 0 && v?.ultimoVisto == Date(timeIntervalSince1970: 1_792_085_400), "fuera de la ventana: 0 peticiones, pero visto")
    check(dev("192.168.8.180")?.alertas.first?.tipo == .muchasCuentas, "«sospechoso» de la API → alerta muchasCuentas")
    check(dev("192.168.8.170")?.fabricante == "Google" && dev("192.168.8.160")?.fabricante == "Samsung", "fabricante por OUI (Google, Samsung)")

    // Umbrales
    func e(_ ip: String, _ seg: Double, _ uri: String = "/", _ st: Int = 200, _ m: String = "GET") -> EntradaCaddy {
        EntradaCaddy(ts: AHORA.addingTimeInterval(-seg), ip: ip, metodo: m, host: "events.adventist.club", uri: uri, agente: nil, status: st, duracion: 0, tamano: 0)
    }
    func alertas(_ c: [EntradaCaddy], api: [DispositivoAPI] = []) -> [Alerta.Tipo] {
        Dispositivos.agregar(caddy: c, arp: [:], leases: [], api: api, bloqueados: [], ahora: AHORA).first?.alertas.map { $0.tipo } ?? []
    }
    let flood = (0..<700).map { e("192.168.8.200", Double($0) * 119.0 / 700.0) }
    check(alertas(flood) == [.muchasPeticiones], "> 300/min dos minutos seguidos → muchasPeticiones")
    let pico = (0..<400).map { e("192.168.8.200", Double($0) * 59.0 / 400.0) }
    check(alertas(pico).isEmpty, "pico de un solo minuto → nada")
    check(alertas((0..<24).map { e("192.168.8.201", Double($0), "/no-existe-\($0)", 404) }).isEmpty, "24 rutas 404 → nada")
    check(alertas((0..<25).map { e("192.168.8.201", Double($0), "/no-existe-\($0)", 404) }) == [.escaneo], "25 rutas 404 → escaneo")
    check(alertas([e("192.168.8.201", 10, "/.env", 404)]) == [.escaneo], "una ruta de escáner (/.env) → escaneo")
    check(alertas([e("192.168.8.201", 700, "/.env", 404)]).isEmpty, "ruta de escáner hace 11 min → nada")
    func apiF(_ f: Int, _ c: Int) -> [DispositivoAPI] {
        [DispositivoAPI(ip: "192.168.8.202", cuenta: nil, fallidos10min: f, ultimoFallo: nil, correosDistintos: c, correos: [], primero: nil, ultimo: nil)]
    }
    check(alertas([], api: apiF(7, 0)).isEmpty && alertas([], api: apiF(8, 0)) == [.muchosFallidos], "fallidos: 7 nada, 8 alerta")
    check(alertas([], api: apiF(0, 3)).isEmpty && alertas([], api: apiF(0, 4)) == [.muchasCuentas], "correos: 3 nada, 4 alerta")
    check(alertas((0..<8).map { e("192.168.8.203", Double($0), "/api/v1/auth/login", 401, "POST") }) == [.muchosFallidos], "8 POST /login 401 en Caddy → muchosFallidos")

    check(Dispositivos.esMACPrivada("da:a1:19:aa:bb:cc") && !Dispositivos.esMACPrivada("f0:18:98:12:34:56"), "bit de MAC privada")
    check(Dispositivos.fabricante("00:16:32:01:02:03") == "Samsung" && Dispositivos.fabricante("12:34:56:78:9a:bc") == nil && Dispositivos.fabricante("aa:aa:aa:00:00:00") == nil, "OUI: Samsung / privada / desconocida")
    check(Dispositivos.normalizarMAC("A:b:C:d:E:f") == "0a:0b:0c:0d:0e:0f" && Dispositivos.normalizarMAC("zz") == nil, "normalizar MAC")

    // Archivo grande: sólo el final
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("nodo-prueba-\(UUID().uuidString).log")
    try? (0..<1000).map { "linea \($0)" }.joined(separator: "\n").appending("\n").write(to: tmp, atomically: true, encoding: .utf8)
    let ul = Dispositivos.ultimasLineas(ruta: tmp.path, maxBytes: 100)
    check(ul.last == "linea 999" && ul.allSatisfy { $0.hasPrefix("linea ") } && ul.count < 12, "ultimasLineas: sólo el final, sin línea cortada", "\(ul)")
    check(Dispositivos.ultimasLineas(ruta: tmp.path, maxLineas: 5) == (995...999).map { "linea \($0)" }, "ultimasLineas: máximo de líneas")
    check(Dispositivos.ultimasLineas(ruta: "/no/existe").isEmpty, "ultimasLineas: archivo que no existe")
    try? FileManager.default.removeItem(at: tmp)
}

// MARK: - Reglas de bloqueo
do {
    typealias R = ReglasBloqueo
    check(R.validar(ip: "192.168.8.50", propias: ["192.168.8.20"], router: "192.168.8.1") == .valida, "validar: equipo de la red")
    check(R.validar(ip: "192.168.8.20", propias: ["192.168.8.20"]) != .valida, "validar: esta Mac no")
    check(R.validar(ip: "192.168.8.1", router: "192.168.8.1") != .valida, "validar: el router no")
    check(R.validar(ip: "127.0.0.1") != .valida && R.validar(ip: "::1") != .valida, "validar: loopback no")
    check(R.validar(ip: "255.255.255.255") != .valida && R.validar(ip: "192.168.8.255") != .valida && R.validar(ip: "192.168.8.0") != .valida, "validar: difusión/red no")
    check(R.validar(ip: "8.8.8.8") != .valida && R.validar(ip: "100.64.1.1") != .valida && R.validar(ip: "169.254.1.1") != .valida, "validar: públicas, CGNAT y 169.254 no")
    check(R.validar(ip: "10.9.9.9") == .valida && R.validar(ip: "172.20.1.5") == .valida && R.validar(ip: "172.32.1.5") != .valida, "validar: rangos privados")
    check(R.validar(ip: "fe80::1c2b:3a4d%en0") == .valida && R.validar(ip: "2001:db8::1") != .valida, "validar: IPv6 de enlace local sí, global no")
    check(R.validar(ip: "hola") != .valida && R.validar(ip: "") != .valida && R.validar(ip: "192.168.8.5 192.168.8.6") != .valida, "validar: texto no")
    if case .rechazada(let t) = R.validar(ip: "192.168.8.1", router: "192.168.8.1") { check(t.contains("router"), "validar: motivo en español") } else { check(false, "validar: motivo") }

    let vacio = R.caddySnippet(ips: [])
    check(vacio.hasPrefix("#") && vacio.split(separator: "\n").count == 1, "snippet sin IPs: sólo un comentario")
    let sn = R.caddySnippet(ips: ["192.168.8.23", "10.9.9.9", "192.168.8.23", "8.8.8.8", "1.2.3.4\" 403\nrespond *", "192.168.8.1 "])
    let esperado = "@nodo_bloqueados remote_ip 10.9.9.9 192.168.8.1 192.168.8.23\nerror @nodo_bloqueados \"Este equipo fue bloqueado por la coordinación del evento\" 403\n"
    check(sn == esperado, "snippet de Caddy exacto (sin duplicados ni inyección)", sn)
    check(R.caddySnippet(ips: ["8.8.8.8"]) == vacio, "snippet: sólo IPs inválidas → comentario")

    let l = R.lineaAuditoria(accion: "bloquear", ip: "192.168.8.131", mac: "DA:A1:19:AA:BB:CC", motivo: "escaneo \"raro\"", donde: ["nodo", "router"], ahora: AHORA)
    let o = (try? JSONSerialization.jsonObject(with: Data(l.utf8))) as? [String: Any]
    check(!l.contains("\n") && o?["accion"] as? String == "bloquear" && o?["mac"] as? String == "da:a1:19:aa:bb:cc"
          && o?["donde"] as? [String] == ["nodo", "router"] && o?["ts"] as? String == "2026-10-15T18:00:00Z" && o?["motivo"] as? String == "escaneo \"raro\"",
          "línea de auditoría JSON", l)
    let l2 = R.lineaAuditoria(accion: "desbloquear", ip: "192.168.8.131", mac: nil, motivo: "", donde: ["nodo"], ahora: AHORA)
    check(((try? JSONSerialization.jsonObject(with: Data(l2.utf8))) as? [String: Any])?["mac"] is NSNull, "auditoría sin MAC → null")
}

// MARK: - Comando
do {
    check(Comando.correr("/bin/echo", ["hola"]) == "hola\n", "Comando: stdout")
    check(Comando.correr("/bin/sh", ["-c", "echo err >&2"]) == "err\n", "Comando: stderr")
    let t0 = Date()
    _ = Comando.correr("/bin/sleep", ["10"], timeout: 0.5)
    check(Date().timeIntervalSince(t0) < 3, "Comando: corta a los 0.5 s")
    check(Comando.correr("/no/existe", []) == "", "Comando: ejecutable inexistente → \"\"")
}

// MARK: - Depurador (casos compartidos con depurar.pl y el backend: pruebas/depurador/casos.txt)
do {
    func repetir(_ s: String, _ n: Int) -> String { String(String(repeating: s, count: n / s.count + 1).prefix(n)) }
    // Secretos de mentira armados aquí (nunca escritos tal cual en el repo); = probar.pl.
    let M: [String: String] = [
        "AIZA": "AI" + "za" + repetir("Xy9_", 35), "AQ": "AQ" + "." + repetir("Ab3-", 40),
        "YA29": "ya" + "29." + repetir("a0B_", 32), "GHP": "gh" + "p_" + repetir("Z9y8", 36),
        "GHO": "gh" + "o_" + repetir("K7j6", 36), "GHPAT": "github" + "_pat_" + repetir("11AB", 48),
        "SK": "sk" + "-ant-" + repetir("q1W2", 32), "XOX": "xo" + "xb-" + repetir("12345-", 24),
        "JWT": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0." + repetir("dGVzdA", 30),
        "B64": repetir("QUJD", 48),
        "PEM": "-----BEGIN " + "PRIVATE KEY-----\n" + repetir("MIIEvQ", 64) + "\n" + repetir("AbCd", 64) + "\n-----END " + "PRIVATE KEY-----",
        "AWS": "AK" + "IA" + "ABCDEFGHIJKLMNOP", "EVP": "evp" + "_" + repetir("Pq7-", 43)]
    func expandir(_ t: String) -> String {
        var t = t
        for (k, v) in M { t = t.replacingOccurrences(of: "{{\(k)}}", with: v) }
        return t
    }
    let equipo = Depurador.normalizarIdentidad(["Mac de Prueba Ñandú", "ana-prueba"])
    let ruta = "\(dir)/../depurador/casos.txt"
    let texto = (try? String(contentsOfFile: ruta, encoding: .utf8)) ?? ""
    check(!texto.isEmpty, "Depurador: casos.txt", ruta)
    var casos: [(nombre: String, partes: [String: String])] = []
    var seccion: String?
    for linea in texto.components(separatedBy: "\n").dropLast() {
        if casos.isEmpty && linea.hasPrefix("#") && !linea.hasPrefix("### ") { continue }
        if linea.hasPrefix("### ") { casos.append((String(linea.dropFirst(4)), [:])); seccion = nil; continue }
        if ["--- entrada", "--- esperado", "--- procesos"].contains(linea) { seccion = String(linea.dropFirst(4)); casos[casos.count - 1].partes[seccion!] = ""; continue }
        if let s = seccion { casos[casos.count - 1].partes[s, default: ""] += linea + "\n" }
    }
    check(casos.count >= 12, "Depurador: casos leídos", "\(casos.count)")
    for c in casos {
        let entrada = expandir(c.partes["entrada"] ?? "")
        if let e = c.partes["esperado"] {
            let o = Depurador.texto(entrada, equipo: equipo)
            check(o == e, "Depurador: \(c.nombre)", "obtuvo:\n\(o)")
            check(Depurador.tipos(o).isEmpty, "Depurador: \(c.nombre) queda sin secretos", "\(Depurador.tipos(o))")
        }
        if let e = c.partes["procesos"] {
            let o = Depurador.procesos(entrada, equipo: equipo)
            check(o == e, "Depurador (procesos): \(c.nombre)", "obtuvo:\n\(o)")
        }
    }
    // El incidente real: la llave no queda ni en modo texto ni en modo procesos.
    let incidente = "  1 /usr/bin/curl --header X-Goog-Api-Key: \(M["AQ"]!)"
    check(!Depurador.texto(incidente, equipo: []).contains(M["AQ"]!) && !Depurador.procesos(incidente, equipo: []).contains("X-Goog"),
          "Depurador: incidente X-Goog-Api-Key")
    // Un hallazgo nunca guarda en «detalle» lo que el Depurador quita.
    let h = Hallazgo(id: "x", nivel: .info, titulo: "t", guia: "g", detalle: "proceso con --token \(M["GHP"]!) y MAC 3c:22:fb:1a:2b:3c")
    check(!h.detalle.contains(M["GHP"]!) && h.detalle.contains("xx:xx:xx:xx:2b:3c"), "Hallazgo: detalle depurado", h.detalle)
    // ps: sólo columnas sin argumentos.
    check(Comando.permitido("/bin/ps", ["-axo", "comm="]) && Comando.permitido("/bin/ps", ["-axo", "pid=,comm="]), "ps: comm permitido")
    for malos in [["-axo", "args="], ["-axo", "pid,command"], ["-ef"], ["aux"], ["-axww", "-o", "comm="], ["-axo", "comm=", "-ww"], []] {
        check(!Comando.permitido("/bin/ps", malos), "ps: \(malos) rechazado")
    }
    check(Comando.correr("/bin/ps", ["-axo", "args="]) == "", "Comando: ps con argumentos no corre")
    check(Comando.permitido("/usr/sbin/netstat", ["-rn"]), "otras órdenes sin cambio")
}

print("\nAnalizadores: \(pasadas) pruebas bien, \(fallidas) mal")

if args.contains("--real") {
    let app = ProcessInfo.processInfo.environment["NODO_APP_EJECUTABLE"] ?? "\(NSHomeDirectory())/Applications/Nodo Camporee.app/Contents/MacOS/Nodo Camporee"
    print("\n== Interferencias en esta Mac ==")
    for h in Interferencias.recolectar(appEjecutable: app) { print("[\(h.nivel.rawValue)] \(h.titulo)") }
    print("\n== Seguridad de esta Mac ==")
    for h in SeguridadMac.recolectar(appEjecutable: app) { print("[\(h.nivel.rawValue)] \(h.titulo)") }
}
exit(fallidas == 0 ? 0 : 1)
