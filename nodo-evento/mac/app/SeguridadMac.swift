// «Seguridad de esta Mac» durante el evento: la Mac del nodo está en una red llena de teléfonos
// desconocidos. Revisa (sólo lectura, sin pedir contraseña de administrador) FileVault, firewall,
// servicios de «Compartir», inicio automático, bloqueo de pantalla y AirDrop. Nunca cambia nada:
// cada hallazgo dice paso a paso dónde cambiarlo en Ajustes del Sistema (macOS 13–15).
//
// Puertos en escucha: `netstat -anv -p tcp` muestra TODOS los sockets en escucha con su proceso sin
// ser root (lsof sólo vería los del usuario).
import Foundation

enum SeguridadMac {
    struct Muestras {
        var fdesetup = ""              // fdesetup status
        var firewallGlobal = ""        // socketfilterfw --getglobalstate
        var firewallBloqueoTotal = ""  // socketfilterfw --getblockall
        var firewallSigilo = ""        // socketfilterfw --getstealthmode
        var firewallApps = ""          // socketfilterfw --listapps
        var sharing = ""               // sharing -l
        var netstatEscucha = ""        // netstat -anv -p tcp
        var procesos = ""              // ps -axo comm=
        var mediaSharing = ""          // defaults read com.apple.amp.mediasharingd
        var cupsctl = ""               // cupsctl
        var loginwindow = ""           // defaults read /Library/Preferences/com.apple.loginwindow
        var bloqueoPantalla = ""       // sysadminctl -screenLock status
        var salvapantallas = ""        // defaults read com.apple.screensaver
        var airdrop = ""               // defaults read com.apple.sharingd DiscoverableMode
        var pantallaCompartida = ""    // launchctl print system/com.apple.screensharing
    }

    struct Escucha: Equatable { let puerto: Int; let proceso: String?; let expuesta: Bool }

    /// Sockets TCP en escucha. `expuesta` = escucha en todas las direcciones o en una que no es
    /// de loopback (los teléfonos podrían conectarse).
    static func escuchas(_ netstat: String) -> [Escucha] {
        var r: [Escucha] = []
        for l in netstat.split(separator: "\n") where l.contains("LISTEN") {
            let t = l.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
            guard t.count >= 4, t[0].hasPrefix("tcp") else { continue }
            let local = t[3]
            guard let punto = local.lastIndex(of: "."), let puerto = Int(local[local.index(after: punto)...]) else { continue }
            let dir = String(local[..<punto])
            let expuesta = !(dir.hasPrefix("127.") || dir == "::1" || dir.hasPrefix("fe80:"))
            let proc = t.first { $0.range(of: #"^[A-Za-z][^:]*:[0-9]+$"#, options: .regularExpression) != nil }
                .map { String($0[..<$0.lastIndex(of: ":")!]) }
            r.append(Escucha(puerto: puerto, proceso: proc, expuesta: expuesta))
        }
        return r
    }

    static func valorDefaults(_ texto: String, _ clave: String) -> String? {
        for l in texto.split(separator: "\n") {
            let s = l.trimmingCharacters(in: .whitespaces)
            for k in ["\"\(clave)\"", clave] where s.hasPrefix(k + " =") || s.hasPrefix(k + "=") {
                let v = s.split(separator: "=", maxSplits: 1).last.map { $0.trimmingCharacters(in: CharacterSet(charactersIn: " ;\"")) }
                return v
            }
        }
        return nil
    }

    static func analizar(_ m: Muestras, appEjecutable: String) -> [Hallazgo] {
        var h: [Hallazgo] = []
        let compartir = "Ajustes del Sistema › General › Compartir"
        let esc = escuchas(m.netstatEscucha).filter { $0.expuesta }
        func escucha(_ p: Int) -> Escucha? { esc.first { $0.puerto == p } }
        let procs = Interferencias.procesos(m.procesos)

        // FileVault
        let fv = m.fdesetup.lowercased()
        if fv.contains("filevault is on") {
            h.append(Hallazgo(id: "filevault", nivel: .ok, titulo: "FileVault encendido", guia: "El disco está cifrado: si roban la Mac, los datos de los participantes están protegidos.", detalle: m.fdesetup.recortadoSeg))
        } else if fv.contains("filevault is off") {
            h.append(Hallazgo(id: "filevault", nivel: .problema, titulo: "FileVault está apagado",
                              guia: "La Mac guarda datos de participantes (muchos son menores). Enciéndelo en casa, antes del evento: Ajustes del Sistema › Privacidad y seguridad › FileVault › Activar. Guarda la llave de recuperación en un lugar seguro. (Cifrar tarda un rato; la Mac se puede usar mientras.)",
                              detalle: m.fdesetup.recortadoSeg))
        } else if fv.contains("progress") {
            h.append(Hallazgo(id: "filevault", nivel: .info, titulo: "FileVault se está cifrando", guia: "Deja la Mac conectada a la corriente hasta que termine.", detalle: m.fdesetup.recortadoSeg))
        } else {
            h.append(Hallazgo(id: "filevault", nivel: .info, titulo: "No se pudo saber si FileVault está encendido", guia: "Revísalo en Ajustes del Sistema › Privacidad y seguridad › FileVault.", detalle: m.fdesetup.recortadoSeg))
        }

        // Firewall
        typealias FW = Interferencias.Firewall
        let fwPasos = "Ajustes del Sistema › Red › Firewall"
        switch FW.encendido(m.firewallGlobal) {
        case .some(true):
            if FW.bloqueaTodo(global: m.firewallGlobal, bloqueoTotal: m.firewallBloqueoTotal) {
                h.append(Hallazgo(id: "firewall", nivel: .info, titulo: "Firewall encendido y bloqueando todo",
                                  guia: "Es muy seguro, pero así los teléfonos no llegan al nodo: en \(fwPasos) › Opciones… apaga «Bloquear todas las conexiones entrantes» y permite sólo a Nodo Camporee.", detalle: m.firewallBloqueoTotal.recortadoSeg))
            } else {
                h.append(Hallazgo(id: "firewall", nivel: .ok, titulo: "Firewall encendido", guia: "Bien: macOS sólo deja entrar conexiones a las apps permitidas.", detalle: m.firewallGlobal.recortadoSeg))
            }
            switch FW.sigilo(m.firewallSigilo) {
            case .some(true):
                h.append(Hallazgo(id: "firewall-sigilo", nivel: .ok, titulo: "Modo encubierto encendido", guia: "La Mac no contesta a quien la rastrea en la red.", detalle: m.firewallSigilo.recortadoSeg))
            case .some(false):
                h.append(Hallazgo(id: "firewall-sigilo", nivel: .info, titulo: "Modo encubierto apagado",
                                  guia: "Recomendado para el evento: \(fwPasos) › Opciones… › activa «Modo encubierto». No afecta a los teléfonos que usan el nodo.", detalle: m.firewallSigilo.recortadoSeg))
            case .none: break
            }
            let apps = FW.apps(m.firewallApps)
            let nodo = FW.entrada(apps, ejecutable: appEjecutable)
            let otras = apps.filter { a in
                a.permitir == true && a != nodo && (a.ruta as NSString).lastPathComponent != "limactl"
                    && !a.ruta.hasPrefix("/System/") && !a.ruta.hasPrefix("/usr/libexec/")
            }
            if !otras.isEmpty {
                let nombres = otras.map { nombreLegible($0.ruta) }
                let muestra = nombres.prefix(6).joined(separator: ", ") + (nombres.count > 6 ? " y \(nombres.count - 6) más" : "")
                h.append(Hallazgo(id: "firewall-otras", nivel: .info, titulo: "Otras \(otras.count) apps pueden recibir conexiones",
                                  guia: "Durante el evento sólo hace falta permitir a Nodo Camporee (y limactl). En \(fwPasos) › Opciones… puedes poner las demás en «Bloquear conexiones entrantes»: \(muestra).",
                                  detalle: otras.map { $0.ruta }.joined(separator: "\n")))
            }
        case .some(false):
            h.append(Hallazgo(id: "firewall", nivel: .aviso, titulo: "El firewall de macOS está apagado",
                              guia: "Enciéndelo: \(fwPasos) › activar. Luego, en Opciones…, permite «Nodo Camporee» y limactl (Permitir conexiones entrantes). Si al encender el nodo aparece «¿Permitir conexiones entrantes?», pulsa Permitir.",
                              detalle: m.firewallGlobal.recortadoSeg))
        case .none:
            h.append(Hallazgo(id: "firewall", nivel: .info, titulo: "No se pudo leer el firewall", guia: "Revísalo en \(fwPasos).", detalle: m.firewallGlobal.recortadoSeg))
        }

        // Compartir archivos (SMB 445 / AFP 548)
        let invitado = m.sharing.range(of: #"guest access:\s*1"#, options: .regularExpression) != nil
        if let e = escucha(445) ?? escucha(548) {
            h.append(Hallazgo(id: "compartir-archivos", nivel: invitado ? .problema : .aviso,
                              titulo: invitado ? "Compartir archivos está encendido (con acceso de invitado)" : "Compartir archivos está encendido",
                              guia: "Cualquiera en la red del evento podría ver carpetas de esta Mac. Apágalo: \(compartir) › «Compartir archivos».",
                              detalle: "Escuchando en el puerto \(e.puerto)" + (invitado ? "; hay carpetas con acceso de invitado" : "")))
        } else {
            h.append(Hallazgo(id: "compartir-archivos", nivel: .ok, titulo: "Compartir archivos apagado", guia: "", detalle: ""))
        }

        // Gestión remota (ARD) y Compartir pantalla (VNC 5900)
        let ard = procs.contains("ARDAgent") || escucha(3283) != nil
        if ard {
            h.append(Hallazgo(id: "gestion-remota", nivel: .aviso, titulo: "Gestión remota está encendida",
                              guia: "Permite controlar esta Mac por la red. Apágala durante el evento: \(compartir) › «Gestión remota».",
                              detalle: procs.contains("ARDAgent") ? "Proceso ARDAgent en marcha" : "Escuchando en el puerto 3283"))
        }
        let vnc = escucha(5900) != nil || m.pantallaCompartida.contains("state = running")
        if vnc && !ard {
            h.append(Hallazgo(id: "compartir-pantalla", nivel: .aviso, titulo: "Compartir pantalla está encendido",
                              guia: "Alguien en la red podría intentar ver y manejar esta Mac. Apágalo durante el evento: \(compartir) › «Compartir pantalla».",
                              detalle: "Escuchando en el puerto 5900"))
        } else if !vnc && !ard {
            h.append(Hallazgo(id: "compartir-pantalla", nivel: .ok, titulo: "Compartir pantalla y Gestión remota apagados", guia: "", detalle: ""))
        }

        // Sesión remota (SSH 22)
        if let e = escucha(22) {
            h.append(Hallazgo(id: "sesion-remota", nivel: .aviso, titulo: "Sesión remota (SSH) está encendida",
                              guia: "Permite entrar a esta Mac con usuario y contraseña desde la red. Apágala durante el evento: \(compartir) › «Sesión remota».",
                              detalle: "Escuchando en el puerto 22" + (e.proceso.map { " (\($0))" } ?? "")))
        } else {
            h.append(Hallazgo(id: "sesion-remota", nivel: .ok, titulo: "Sesión remota (SSH) apagada", guia: "", detalle: ""))
        }

        // Compartir contenido multimedia
        let media = ["home-sharing-enabled", "public-sharing-enabled"].filter { valorDefaults(m.mediaSharing, $0) == "1" }
        if !media.isEmpty {
            h.append(Hallazgo(id: "compartir-multimedia", nivel: .aviso, titulo: "Compartir contenido multimedia está encendido",
                              guia: "Muestra tu biblioteca de música y videos a otros equipos de la red. Apágalo: \(compartir) › «Compartir contenido multimedia».",
                              detalle: media.joined(separator: ", ")))
        }

        // Compartir impresoras
        if m.cupsctl.contains("_share_printers=1") {
            h.append(Hallazgo(id: "compartir-impresoras", nivel: .aviso, titulo: "Compartir impresoras está encendido",
                              guia: "Otros equipos de la red podrían mandar trabajos a tus impresoras. Apágalo: \(compartir) › «Compartir impresoras».",
                              detalle: "cupsctl: _share_printers=1"))
        }

        // Receptor AirPlay (ControlCenter en 7000/5000)
        if esc.contains(where: { ($0.puerto == 7000 || $0.puerto == 5000) && ($0.proceso ?? "").hasPrefix("ControlCe") }) {
            h.append(Hallazgo(id: "receptor-airplay", nivel: .info, titulo: "Receptor AirPlay encendido",
                              guia: "Otros podrían intentar proyectar a esta Mac. Si no lo usas, apágalo: Ajustes del Sistema › General › AirDrop y Handoff › «Receptor AirPlay», o permítelo sólo al «Usuario actual».",
                              detalle: "ControlCenter escucha en 5000/7000"))
        }

        // Inicio de sesión automático e invitado
        if valorDefaults(m.loginwindow, "autoLoginUser") != nil {
            h.append(Hallazgo(id: "inicio-automatico", nivel: .problema, titulo: "La Mac entra sola, sin contraseña",
                              guia: "Cualquiera que la encienda entra a tu usuario. Apágalo: Ajustes del Sistema › Usuarios y grupos › «Iniciar sesión automáticamente como» › Desactivado.",
                              detalle: "loginwindow: autoLoginUser presente (nombre omitido)"))
        } else {
            h.append(Hallazgo(id: "inicio-automatico", nivel: .ok, titulo: "Pide contraseña al encender", guia: "", detalle: ""))
        }
        if valorDefaults(m.loginwindow, "GuestEnabled") == "1" {
            h.append(Hallazgo(id: "invitado", nivel: .info, titulo: "El usuario invitado está activado",
                              guia: "Durante el evento es mejor apagarlo: Ajustes del Sistema › Usuarios y grupos › Usuario invitado › desactivar «Permitir que los invitados inicien sesión».",
                              detalle: "loginwindow: GuestEnabled = 1"))
        }

        // Bloqueo de pantalla
        h.append(bloqueo(m))

        // AirDrop
        let ad = m.airdrop.recortadoSeg
        if ad == "Everyone" {
            h.append(Hallazgo(id: "airdrop", nivel: .aviso, titulo: "AirDrop está abierto a todos",
                              guia: "Cualquier teléfono del evento puede mandarle cosas a esta Mac. Cámbialo a «Sólo contactos» o «No recibir»: Ajustes del Sistema › General › AirDrop y Handoff › AirDrop (en macOS 13: Centro de control › AirDrop).",
                              detalle: "DiscoverableMode = Everyone"))
        } else {
            h.append(Hallazgo(id: "airdrop", nivel: .ok, titulo: "AirDrop no está abierto a todos", guia: "", detalle: ad.isEmpty || ad.contains("does not exist") ? "Valor predeterminado (Sólo contactos)" : "DiscoverableMode = \(ad)"))
        }
        return Interferencias.ordenar(h)
    }

    static func bloqueo(_ m: Muestras) -> Hallazgo {
        let pasos = "Ajustes del Sistema › Pantalla bloqueada › «Solicitar contraseña tras iniciar el protector de pantalla o apagar la pantalla» › Inmediatamente"
        let t = m.bloqueoPantalla.lowercased()
        var segundos: Int?; var apagado = false; var sabe = false
        if t.contains("screenlock delay is immediate") { segundos = 0; sabe = true }
        else if let r = t.range(of: #"screenlock delay is (\d+) seconds"#, options: .regularExpression) {
            segundos = Int(t[r].filter { $0.isNumber }); sabe = true
        } else if t.contains("screenlock is off") { apagado = true; sabe = true }
        if !sabe, let ask = SeguridadMac.valorDefaults(m.salvapantallas, "askForPassword") {
            sabe = true
            if ask == "0" { apagado = true } else { segundos = Int(SeguridadMac.valorDefaults(m.salvapantallas, "askForPasswordDelay") ?? "0") ?? 0 }
        }
        if !sabe {
            return Hallazgo(id: "bloqueo-pantalla", nivel: .info, titulo: "No se pudo comprobar el bloqueo de pantalla",
                            guia: "Revisa que pida contraseña: \(pasos).", detalle: m.bloqueoPantalla.recortadoSeg)
        }
        if apagado {
            return Hallazgo(id: "bloqueo-pantalla", nivel: .aviso, titulo: "La pantalla no pide contraseña",
                            guia: "Si te alejas de la Mac, cualquiera puede usarla. Actívalo: \(pasos).", detalle: m.bloqueoPantalla.recortadoSeg)
        }
        if let s = segundos, s > 300 {
            return Hallazgo(id: "bloqueo-pantalla", nivel: .aviso, titulo: "La pantalla tarda \(s / 60) min en pedir contraseña",
                            guia: "Durante el evento conviene que la pida enseguida: \(pasos).", detalle: m.bloqueoPantalla.recortadoSeg)
        }
        return Hallazgo(id: "bloqueo-pantalla", nivel: .ok, titulo: "La pantalla pide contraseña", guia: "", detalle: m.bloqueoPantalla.recortadoSeg)
    }

    static func nombreLegible(_ ruta: String) -> String {
        if let r = ruta.range(of: ".app", options: .backwards) {
            return ((String(ruta[..<r.lowerBound]) as NSString).lastPathComponent)
        }
        return (ruta as NSString).lastPathComponent
    }

    static func muestrasDeEstaMac() -> Muestras {
        let fw = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        var m = Muestras()
        m.fdesetup = Comando.correr("/usr/bin/fdesetup", ["status"])
        m.firewallGlobal = Comando.correr(fw, ["--getglobalstate"])
        m.firewallBloqueoTotal = Comando.correr(fw, ["--getblockall"])
        m.firewallSigilo = Comando.correr(fw, ["--getstealthmode"])
        m.firewallApps = Comando.correr(fw, ["--listapps"])
        m.sharing = Comando.correr("/usr/sbin/sharing", ["-l"])
        m.netstatEscucha = Comando.correr("/usr/sbin/netstat", ["-anv", "-p", "tcp"])
        m.procesos = Comando.correr("/bin/ps", ["-axo", "comm="])
        m.mediaSharing = Comando.correr("/usr/bin/defaults", ["read", "com.apple.amp.mediasharingd"])
        m.cupsctl = Comando.correr("/usr/sbin/cupsctl", [])
        m.loginwindow = Comando.correr("/usr/bin/defaults", ["read", "/Library/Preferences/com.apple.loginwindow"])
        m.bloqueoPantalla = Comando.correr("/usr/sbin/sysadminctl", ["-screenLock", "status"])
        m.salvapantallas = Comando.correr("/usr/bin/defaults", ["read", "com.apple.screensaver"])
        m.airdrop = Comando.correr("/usr/bin/defaults", ["read", "com.apple.sharingd", "DiscoverableMode"])
        m.pantallaCompartida = Comando.correr("/bin/launchctl", ["print", "system/com.apple.screensharing"])
        return m
    }

    static func recolectar(appEjecutable: String) -> [Hallazgo] {
        analizar(muestrasDeEstaMac(), appEjecutable: appEjecutable)
    }
}

private extension String {
    var recortadoSeg: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
