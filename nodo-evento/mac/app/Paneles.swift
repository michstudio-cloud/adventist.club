// v3: Interferencias, Seguridad de la Mac, Dispositivos conectados (con bloqueos), Nodos vecinos y
// Actualizaciones. Todo en español sencillo; nada cambia ajustes de otras apps ni de macOS sin que
// alguien pulse un botón (y lo de macOS nunca: sólo se explica cómo).
import AppKit
import Combine
import SwiftUI

// MARK: - Revisiones (interferencias y seguridad), en segundo plano

@MainActor
final class Revisiones: ObservableObject {
    static let compartido = Revisiones()
    @Published var interferencias: [Hallazgo] = []
    @Published var seguridad: [Hallazgo] = []
    @Published var cuandoInterferencias: Date?
    @Published var cuandoSeguridad: Date?
    @Published var revisando = false
    private var timer: Timer?

    var ejecutable: String { Bundle.main.executablePath ?? "" }

    func arrancar() {
        revisarTodo()
        timer?.invalidate()
        // Cada 5 minutos: una VPN que se conecta a media mañana se nota sola.
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.revisarTodo() }
        }
    }

    func revisarTodo(alTerminar: (([Hallazgo]) -> Void)? = nil) {
        guard !revisando else { return }
        revisando = true
        let exe = ejecutable
        Task.detached {
            let i = Interferencias.recolectar(appEjecutable: exe)
            let s = SeguridadMac.recolectar(appEjecutable: exe)
            await MainActor.run {
                self.interferencias = i; self.cuandoInterferencias = Date()
                self.seguridad = s; self.cuandoSeguridad = Date()
                self.revisando = false
                alTerminar?(i)
            }
        }
    }

    /// Sólo interferencias (para «Encender»: rápido, sin bloquear).
    func revisarInterferencias(alTerminar: @escaping ([Hallazgo]) -> Void) {
        let exe = ejecutable
        Task.detached {
            let i = Interferencias.recolectar(appEjecutable: exe)
            await MainActor.run { self.interferencias = i; self.cuandoInterferencias = Date(); alTerminar(i) }
        }
    }

    static func luz(_ hs: [Hallazgo]) -> Luz {
        if hs.isEmpty { return .gris }
        if hs.contains(where: { $0.nivel == .problema }) { return .rojo }
        if hs.contains(where: { $0.nivel == .aviso }) { return .ambar }
        return .verde
    }
    static func importantes(_ hs: [Hallazgo]) -> [Hallazgo] { hs.filter { $0.nivel == .problema || $0.nivel == .aviso } }
}

extension Nivel {
    var luz: Luz {
        switch self { case .ok: return .verde; case .info: return .gris; case .aviso: return .ambar; case .problema: return .rojo }
    }
}

struct FilaHallazgo: View {
    let h: Hallazgo
    @State private var verDetalle = false
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Foco(luz: h.nivel.luz, tam: 16).padding(.top, 3)
            VStack(alignment: .leading, spacing: 4) {
                Text(h.titulo).font(.system(size: 16, weight: .semibold))
                if !h.guia.isEmpty {
                    Text(h.guia).font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
                }
                if !h.detalle.isEmpty {
                    Button(verDetalle ? "Ocultar detalle técnico" : "Detalle técnico") { verDetalle.toggle() }
                        .buttonStyle(.link).font(.system(size: 12))
                    if verDetalle {
                        Text(h.detalle).font(.system(size: 12, design: .monospaced)).foregroundStyle(.secondary)
                            .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
    }
}

func horaCorta(_ d: Date?) -> String {
    guard let d else { return "todavía no" }
    let f = DateFormatter(); f.dateFormat = "HH:mm"
    return "a las \(f.string(from: d))"
}

// MARK: - Interferencias

struct HojaInterferencias: View {
    @Binding var abierta: Hoja?
    @EnvironmentObject var motor: Motor
    @ObservedObject var rev = Revisiones.compartido
    var body: some View {
        HojaMarco(titulo: "Interferencias", icono: "exclamationmark.shield", abierta: $abierta) {
            Text("VPN, filtros de red, firewall y DNS que pueden impedir que los teléfonos lleguen al nodo. La app NO cambia nada de esas apps: sólo te dice qué hacer durante el evento.")
                .font(.system(size: 14)).foregroundStyle(.secondary)
            HStack {
                Button(rev.revisando ? "Revisando…" : "Revisar interferencias") { rev.revisarTodo() }
                    .controlSize(.large).disabled(rev.revisando)
                Text("Última revisión: \(horaCorta(rev.cuandoInterferencias))").font(.system(size: 13)).foregroundStyle(.secondary)
                Spacer()
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(rev.interferencias) { FilaHallazgo(h: $0) }
                }
            }
        }
    }
}

// MARK: - Seguridad de la Mac (+ contraseña del Wi-Fi del evento)

struct HojaSeguridad: View {
    @Binding var abierta: Hoja?
    @EnvironmentObject var motor: Motor
    @ObservedObject var rev = Revisiones.compartido
    @State private var claveRouter = ""
    @State private var claveNueva = ""
    @State private var ssid = ""
    @State private var confirmar = false

    var redAbierta: Bool { motor.cfg("RED_MODO") == "publica" && motor.cfg("PUBLICA_ABIERTA") != "0" }
    var routerAutomatico: Bool { ["glinet", "openwrt", "mikrotik"].contains(motor.cfg("ROUTER_TIPO").isEmpty ? "glinet" : motor.cfg("ROUTER_TIPO")) && motor.cfg("RED_MODO") != "google" }

    var body: some View {
        HojaMarco(titulo: "Seguridad de la Mac", icono: "lock.laptopcomputer", abierta: $abierta) {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Revisión de sólo lectura: la app no cambia nada de macOS. Cada punto dice dónde ajustarlo.")
                        .font(.system(size: 14)).foregroundStyle(.secondary)
                    HStack {
                        Button(rev.revisando ? "Revisando…" : "Revisar otra vez") { rev.revisarTodo() }.disabled(rev.revisando)
                        Text("Última revisión: \(horaCorta(rev.cuandoSeguridad))").font(.system(size: 13)).foregroundStyle(.secondary)
                        Spacer()
                    }
                    ForEach(rev.seguridad) { FilaHallazgo(h: $0) }
                    Divider().padding(.vertical, 6)
                    wifi
                }
            }
        }
    }

    var wifi: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Wi-Fi del evento").font(.system(size: 20, weight: .bold))
            FilaHallazgo(h: Hallazgo(
                id: "wifi", nivel: redAbierta ? .aviso : (motor.secretosPresentes[.wifi] == true ? .ok : .info),
                titulo: redAbierta ? "El Wi-Fi del evento está ABIERTO" : (motor.secretosPresentes[.wifi] == true ? "Wi-Fi con contraseña (WPA2)" : "Contraseña del Wi-Fi: no guardada en esta Mac"),
                guia: "Recomendado: Wi-Fi con contraseña WPA2 que sólo tengan jueces, coordinación y directores (no abierto). Al cambiarla, el QR «Conéctate al Wi-Fi» se actualiza solo; los teléfonos ya conectados la pedirán una vez.",
                detalle: "Red: \(motor.cfg("RED_MODO").isEmpty ? "sin elegir" : motor.cfg("RED_MODO")) · router: \(motor.cfg("ROUTER_TIPO"))"))
            if routerAutomatico {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                    GridRow {
                        Text("Contraseña del router (panel)").font(.system(size: 14, weight: .semibold))
                        SecureField("no se guarda", text: $claveRouter).textFieldStyle(.roundedBorder)
                    }
                    GridRow {
                        Text("Nombre del Wi-Fi (opcional)").font(.system(size: 14, weight: .semibold))
                        TextField(motor.cfg("WIFI_SSID").isEmpty ? "sin cambiar" : motor.cfg("WIFI_SSID"), text: $ssid).textFieldStyle(.roundedBorder)
                    }
                    GridRow {
                        Text("Contraseña nueva del Wi-Fi").font(.system(size: 14, weight: .semibold))
                        HStack {
                            SecureField("8 a 63 caracteres", text: $claveNueva).textFieldStyle(.roundedBorder)
                            Button("Generar") { claveNueva = Self.generar() }
                        }
                    }
                }
                HStack {
                    Button("Ver qué cambiará") {
                        motor.correr(["wifi-clave", "--simular"] + (ssid.isEmpty ? [] : ["--ssid", ssid]), titulo: "Wi-Fi: simulación",
                                     extra: ["WIFI_PASSWORD": claveNueva])
                    }.disabled(claveNueva.count < 8 || motor.ocupado)
                    Button("Cambiar contraseña del Wi-Fi") { confirmar = true }
                        .buttonStyle(.borderedProminent).disabled(claveNueva.count < 8 || claveRouter.isEmpty || motor.ocupado)
                }
                if !motor.log.isEmpty { VistaLog(alto: 120) }
            } else {
                Text("Con Google Wifi / Nest: Google Home › Wi-Fi › Configuración de red › contraseña. Luego guarda la nueva en Ajustes › Avanzado (contraseña del Wi-Fi) para el QR.")
                    .font(.system(size: 14))
            }
        }
        .alert("¿Cambiar la contraseña del Wi-Fi?", isPresented: $confirmar) {
            Button("Cambiar") { cambiar() }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("El Wi-Fi se reinicia unos segundos y todos los teléfonos tendrán que escribir la contraseña nueva (o escanear el QR otra vez).")
        }
    }

    func cambiar() {
        let nueva = claveNueva
        var args = ["wifi-clave"]
        if !ssid.isEmpty { args += ["--ssid", ssid] }
        motor.correr(args, titulo: "Cambiando la contraseña del Wi-Fi",
                     extra: ["NODO_ROUTER_PASS": claveRouter, "WIFI_PASSWORD": nueva]) { code in
            if code == 0 {
                Llavero.guardar(.wifi, nueva)
                if !ssid.isEmpty { motor.guardarConfig("WIFI_SSID", ssid) }
                motor.refrescarSecretos()
                claveNueva = ""; claveRouter = ""
            }
        }
    }

    /// Fácil de dictar: palabras + números, sin caracteres ambiguos ni comillas.
    static func generar() -> String {
        let palabras = ["sol", "rio", "pino", "luna", "roca", "faro", "nube", "lago", "cielo", "monte", "valle", "brasa", "canto", "fuego", "senda"]
        var b = [UInt8](repeating: 0, count: 6)
        _ = SecRandomCopyBytes(kSecRandomDefault, b.count, &b)
        return "\(palabras[Int(b[0]) % palabras.count])-\(palabras[Int(b[1]) % palabras.count])-\(Int(b[2]) % 90 + 10)\(Int(b[3]) % 90 + 10)"
    }
}

// MARK: - Bloqueos (nodo siempre; router si se puede). Todo queda auditado.

struct RegistroBloqueo: Codable, Equatable, Identifiable {
    var id: String { ip }
    var ip: String
    var mac: String?
    var motivo: String
    var cuando: Double
    var router: Bool
}

enum Bloqueos {
    static var run: String { Actualizador.carpetaRun }
    static var archivo: String { run + "/bloqueados.json" }
    static var auditoria: String { (run as NSString).deletingLastPathComponent + "/auditoria-bloqueos.jsonl" }

    static func leer() -> [RegistroBloqueo] {
        guard let d = FileManager.default.contents(atPath: archivo) else { return [] }
        return (try? JSONDecoder().decode([RegistroBloqueo].self, from: d)) ?? []
    }

    /// Escribe la lista para la puerta (run/bloqueados.json) y para Caddy (bloqueos/bloqueados.caddy).
    static func guardar(_ lista: [RegistroBloqueo], nodoDir: String) throws {
        let d = try JSONEncoder().encode(lista)
        try d.write(to: URL(fileURLWithPath: archivo), options: .atomic)
        if !nodoDir.isEmpty {
            let dir = nodoDir + "/bloqueos"
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try ReglasBloqueo.caddySnippet(ips: lista.map(\.ip)).write(toFile: dir + "/bloqueados.caddy", atomically: true, encoding: .utf8)
        }
    }

    static func auditar(_ linea: String) {
        let url = URL(fileURLWithPath: auditoria)
        if !FileManager.default.fileExists(atPath: auditoria) { FileManager.default.createFile(atPath: auditoria, contents: nil) }
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile(); h.write(Data((linea + "\n").utf8)); try? h.close()
        }
    }

    static func historial(_ n: Int = 30) -> [String] {
        guard let s = try? String(contentsOfFile: auditoria, encoding: .utf8) else { return [] }
        return Array(s.split(separator: "\n").map(String.init).suffix(n).reversed())
    }
}

// MARK: - Dispositivos conectados

@MainActor
final class Monitor: ObservableObject {
    @Published var lista: [Dispositivo] = []
    @Published var bloqueos: [RegistroBloqueo] = Bloqueos.leer()
    @Published var cuentasFrenadas: [[String: Any]] = []
    @Published var leases: [Lease] = []
    @Published var actualizado: Date?
    @Published var miIP = ""
    @Published var router = ""
    var claveRouter = ""
    private var timer: Timer?
    private var leyendo = false

    func arrancar(motor: Motor) {
        refrescar(motor: motor)
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refrescar(motor: motor) }
        }
    }
    func parar() { timer?.invalidate(); timer = nil }

    nonisolated static func correrMotor(_ ruta: String, _ args: [String], extra: [String: String] = [:]) -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [ruta] + args
        p.environment = Motor.entorno(para: args, extra: extra)
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return Data() }
        let d = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return d
    }

    func refrescar(motor: Motor) {
        guard !leyendo else { return }
        leyendo = true
        let ruta = motor.rutaMotor
        let clave = claveRouter
        let tipo = motor.cfg("ROUTER_TIPO")
        let bloq = Set(Bloqueos.leer().map(\.ip))
        Task.detached {
            let datos = Monitor.correrMotor(ruta, ["dispositivos"])
            let m = Dispositivos.leerMotor(datos)
            let lineas = Dispositivos.ultimasLineas(ruta: m.rutaAcceso)
            let caddy = lineas.compactMap(Dispositivos.entradaCaddy)
            let leases: [Lease]
            if !clave.isEmpty {
                let txt = String(decoding: Monitor.correrMotor(ruta, ["router-clientes"], extra: ["NODO_ROUTER_PASS": clave]), as: UTF8.self)
                leases = tipo == "mikrotik" ? Dispositivos.leasesMikroTik(txt) : Dispositivos.leasesOpenWrt(txt)
            } else { leases = [] }
            let lista = Dispositivos.agregar(caddy: caddy, arp: m.arp, leases: leases, api: m.api, bloqueados: bloq, ahora: Date())
            let frenadas = (try? JSONSerialization.jsonObject(with: Monitor.correrMotor(ruta, ["cuentas", "bloqueadas"]))) as? [String: Any]
            await MainActor.run {
                self.lista = lista; self.leases = leases; self.miIP = m.miIP; self.router = m.router
                self.cuentasFrenadas = (frenadas?["cuentas"] as? [[String: Any]]) ?? []
                self.bloqueos = Bloqueos.leer()
                self.actualizado = Date()
                self.leyendo = false
            }
        }
    }
}

struct HojaDispositivos: View {
    @Binding var abierta: Hoja?
    @EnvironmentObject var motor: Motor
    @StateObject private var mon = Monitor()
    @State private var aBloquear: Dispositivo?
    @State private var motivo = ""
    @State private var enRouter = true
    @State private var claveRouter = ""
    @State private var mensaje = ""
    @State private var verHistorial = false

    var routerAutomatico: Bool {
        let t = motor.cfg("ROUTER_TIPO").isEmpty ? "glinet" : motor.cfg("ROUTER_TIPO")
        return ["glinet", "openwrt", "mikrotik"].contains(t) && motor.cfg("RED_MODO") != "google"
    }

    var body: some View {
        HojaMarco(titulo: "Dispositivos conectados", icono: "iphone.radiowaves.left.and.right", abierta: $abierta) {
            HStack(spacing: 12) {
                Text("\(mon.lista.count) equipo\(mon.lista.count == 1 ? "" : "s") en 10 min · \(mon.lista.filter { !$0.alertas.isEmpty }.count) con alertas · actualizado \(horaCorta(mon.actualizado))")
                    .font(.system(size: 14)).foregroundStyle(.secondary)
                Spacer()
                if routerAutomatico {
                    SecureField("contraseña del router (nombres y MAC)", text: $claveRouter).textFieldStyle(.roundedBorder).frame(width: 260)
                        .onSubmit { mon.claveRouter = claveRouter; mon.refrescar(motor: motor) }
                }
            }
            if motor.estado?.puerta?.activa != true {
                Text("Sin la «puerta» del nodo todos los teléfonos se ven con la misma IP: enciende el nodo con esta versión.")
                    .font(.system(size: 13)).foregroundStyle(Luz.ambar.color)
            }
            ScrollView {
                VStack(spacing: 8) {
                    ForEach(mon.lista) { d in fila(d) }
                    if mon.lista.isEmpty {
                        Text("Todavía nadie usó el sitio en los últimos 10 minutos.").font(.system(size: 15)).foregroundStyle(.secondary).padding(30)
                    }
                    if !mon.cuentasFrenadas.isEmpty { cuentas }
                    if !mon.bloqueos.isEmpty { bloqueados }
                    Button(verHistorial ? "Ocultar registro de bloqueos" : "Registro de bloqueos (auditoría)") { verHistorial.toggle() }.buttonStyle(.link)
                    if verHistorial {
                        Text(Depurador.texto(Bloqueos.historial().joined(separator: "\n"))).font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            if !mensaje.isEmpty { Text(mensaje).font(.system(size: 13, weight: .semibold)) }
        }
        .onAppear { mon.arrancar(motor: motor) }
        .onDisappear { mon.parar() }
        .sheet(item: $aBloquear) { d in dialogoBloqueo(d) }
    }

    func fila(_ d: Dispositivo) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Foco(luz: d.bloqueado ? .rojo : (d.alertas.isEmpty ? .verde : .ambar), tam: 14).padding(.top, 4)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(d.ip).font(.system(size: 15, weight: .bold, design: .monospaced))
                    Text([d.nombre, d.fabricante, d.mac.map { $0 + (d.macPrivada ? " (privada)" : "") }].compactMap { $0 }.joined(separator: " · "))
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                }
                Text(d.cuenta.map { "Sesión: \($0.nombre ?? "") \($0.email ?? "")" } ?? "sin sesión")
                    .font(.system(size: 13))
                Text("\(d.peticionesMin)/min · \(d.peticiones10min) en 10 min · \(d.fallidos10min) inicios fallidos\(d.rutasDistintas404 > 0 ? " · \(d.rutasDistintas404) rutas inexistentes" : "")")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                ForEach(d.alertas, id: \.self) { a in
                    Label(a.texto, systemImage: "exclamationmark.triangle.fill").font(.system(size: 13, weight: .semibold)).foregroundStyle(Luz.ambar.color)
                }
                ForEach(d.notas, id: \.self) { n in Text(n).font(.system(size: 12)).foregroundStyle(.secondary) }
            }
            Spacer()
            if d.bloqueado {
                Button("Desbloquear") { desbloquear(d.ip) }.disabled(motor.ocupado)
            } else {
                Button("Bloquear…") { motivo = d.alertas.first?.texto ?? ""; enRouter = routerAutomatico && d.mac != nil; aBloquear = d }
                    .disabled(motor.ocupado)
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
    }

    var cuentas: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Cuentas frenadas por intentos fallidos").font(.system(size: 17, weight: .bold)).padding(.top, 8)
            Text("Un teléfono que se equivoca muchas veces sólo se frena a sí mismo; si un juez no puede entrar desde ningún lado, desbloquea su cuenta.")
                .font(.system(size: 13)).foregroundStyle(.secondary)
            ForEach(Array(mon.cuentasFrenadas.enumerated()), id: \.offset) { _, c in
                let email = c["email"] as? String ?? ""
                HStack {
                    Text(email).font(.system(size: 14, weight: .semibold))
                    Text((c["bloqueo_global"] as? Bool) == true ? "bloqueada en todos los equipos" : "frenada en \((c["dispositivos"] as? [Any])?.count ?? 0) equipo(s)")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                    Spacer()
                    Button("Desbloquear cuenta") {
                        motor.correr(["cuentas", "desbloquear", email], titulo: "Desbloqueando \(email)") { _ in mon.refrescar(motor: motor) }
                    }.disabled(motor.ocupado)
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    var bloqueados: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Equipos bloqueados").font(.system(size: 17, weight: .bold)).padding(.top, 8)
            ForEach(mon.bloqueos) { b in
                HStack {
                    Text(b.ip).font(.system(size: 14, weight: .semibold, design: .monospaced))
                    Text("\(b.mac ?? "sin MAC") · \(b.router ? "nodo + router" : "nodo") · \(b.motivo)").font(.system(size: 13)).foregroundStyle(.secondary)
                    Spacer()
                    Button("Desbloquear") { desbloquear(b.ip) }.disabled(motor.ocupado)
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    func dialogoBloqueo(_ d: Dispositivo) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Bloquear \(d.ip)").font(.system(size: 22, weight: .bold))
            Text("El nodo dejará de contestarle al instante (puerta y Caddy). Queda en el registro de auditoría con la hora y el motivo.")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            TextField("Motivo (obligatorio)", text: $motivo).textFieldStyle(.roundedBorder)
            if routerAutomatico {
                Toggle("También en el router (por MAC: no se podrá conectar al Wi-Fi)", isOn: $enRouter).disabled(d.mac == nil || d.macPrivada && false)
                if d.mac == nil { Text("Sin MAC conocida: sólo se bloquea en el nodo.").font(.system(size: 12)).foregroundStyle(.secondary) }
                if d.macPrivada { Text("Ojo: es una MAC «privada»; si el teléfono la cambia, vuelve a entrar al Wi-Fi (el bloqueo del nodo por IP sigue).").font(.system(size: 12)).foregroundStyle(.secondary) }
                if enRouter && d.mac != nil {
                    SecureField("Contraseña del router (no se guarda)", text: $claveRouter).textFieldStyle(.roundedBorder)
                }
            } else if motor.cfg("RED_MODO") == "google" {
                Text("Google Wifi / Nest: además, en Google Home › Wi-Fi › Dispositivos › (el equipo) › Pausar.").font(.system(size: 13))
            }
            HStack {
                Spacer()
                Button("Cancelar") { aBloquear = nil }.keyboardShortcut(.cancelAction)
                Button("Bloquear") { bloquear(d) }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(motivo.trimmingCharacters(in: .whitespaces).isEmpty || (enRouter && d.mac != nil && routerAutomatico && claveRouter.isEmpty))
            }
        }
        .padding(24).frame(width: 560)
    }

    func bloquear(_ d: Dispositivo) {
        aBloquear = nil
        let propias = [mon.miIP, motor.estado?.red.ip ?? ""].filter { !$0.isEmpty }
        if case .rechazada(let por) = ReglasBloqueo.validar(ip: d.ip, propias: propias, router: mon.router.isEmpty ? nil : mon.router) {
            mensaje = "No se bloqueó: \(por)"; return
        }
        let conRouter = enRouter && routerAutomatico && d.mac != nil
        var lista = Bloqueos.leer().filter { $0.ip != d.ip }
        lista.append(RegistroBloqueo(ip: d.ip, mac: d.mac, motivo: motivo, cuando: Date().timeIntervalSince1970, router: conRouter))
        do { try Bloqueos.guardar(lista, nodoDir: motor.cfg("NODO_DIR")) } catch { mensaje = "No pude guardar el bloqueo: \(error.localizedDescription)"; return }
        Bloqueos.auditar(ReglasBloqueo.lineaAuditoria(accion: "bloquear", ip: d.ip, mac: d.mac, motivo: motivo,
                                                      donde: conRouter ? ["nodo", "router"] : ["nodo"], ahora: Date()))
        mensaje = "\(d.ip) bloqueado en el nodo."
        let clave = claveRouter
        motor.correr(["caddy-recargar"], titulo: "Bloqueando \(d.ip)") { _ in
            if conRouter, let mac = d.mac {
                motor.correr(["router-bloquear", "--mac", mac], titulo: "Bloqueando \(mac) en el router", extra: ["NODO_ROUTER_PASS": clave], limpiar: false) { code in
                    mensaje = code == 0 ? "\(d.ip) bloqueado en el nodo y en el router." : "\(d.ip) bloqueado en el nodo; el router no aceptó el cambio (revisa la contraseña)."
                    mon.refrescar(motor: motor)
                }
            } else { mon.refrescar(motor: motor) }
        }
    }

    func desbloquear(_ ip: String) {
        let previo = Bloqueos.leer().first { $0.ip == ip }
        let lista = Bloqueos.leer().filter { $0.ip != ip }
        do { try Bloqueos.guardar(lista, nodoDir: motor.cfg("NODO_DIR")) } catch { mensaje = "No pude guardar: \(error.localizedDescription)"; return }
        let enRouter = previo?.router == true && previo?.mac != nil
        Bloqueos.auditar(ReglasBloqueo.lineaAuditoria(accion: "desbloquear", ip: ip, mac: previo?.mac, motivo: "desbloqueo manual",
                                                      donde: enRouter ? ["nodo", "router"] : ["nodo"], ahora: Date()))
        let clave = claveRouter
        motor.correr(["caddy-recargar"], titulo: "Desbloqueando \(ip)") { _ in
            if enRouter, let mac = previo?.mac {
                if clave.isEmpty { mensaje = "\(ip) desbloqueado en el nodo. Para el router escribe su contraseña arriba y vuelve a desbloquear, o quítalo en su panel."; return }
                motor.correr(["router-bloquear", "--mac", mac, "--quitar"], titulo: "Desbloqueando \(mac) en el router", extra: ["NODO_ROUTER_PASS": clave], limpiar: false)
            }
            mon.refrescar(motor: motor)
        }
    }
}

// MARK: - Nodos vecinos

struct EstadoMalla {
    var yo: [String: Any] = [:]
    var vecinos: [[String: Any]] = []
    var rechazados: [[String: Any]] = []
    var actualizado: Double = 0
    var vistosBonjour: [[String: Any]] = []
    /// Bonjour sin respuesta 20 s después de arrancar (con evento): falta el permiso de «Red local».
    var sinPermisoRedLocal = false

    static func leer() -> EstadoMalla {
        var e = EstadoMalla()
        let run = Actualizador.carpetaRun
        if let d = FileManager.default.contents(atPath: run + "/malla-estado.json"),
           let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            e.yo = o["yo"] as? [String: Any] ?? [:]
            e.vecinos = o["vecinos"] as? [[String: Any]] ?? []
            e.rechazados = o["rechazados"] as? [[String: Any]] ?? []
            e.actualizado = (o["actualizado"] as? Double) ?? Double((o["actualizado"] as? Int) ?? 0)
        }
        if let d = FileManager.default.contents(atPath: run + "/malla-vecinos.json"),
           let a = try? JSONSerialization.jsonObject(with: d) as? [[String: Any]] { e.vistosBonjour = a }
        if let d = FileManager.default.contents(atPath: run + "/malla-bonjour.json"),
           let b = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            let desde = (b["desde"] as? Double) ?? 0, t = (b["t"] as? Double) ?? 0
            e.sinPermisoRedLocal = (b["evento"] as? Bool) == true && (b["anunciado"] as? Bool) != true && t - desde > 20
                && Date().timeIntervalSince1970 - t < 60
        }
        return e
    }

    static func texto(_ v: Any?) -> String {
        if let s = v as? String { return s }
        if let a = v as? [String] { return a.joined(separator: ", ") }
        if let n = v as? NSNumber { return n.stringValue }
        return ""
    }
    static func cuando(_ v: Any?) -> String {
        var t: Double = 0
        if let d = v as? Double { t = d } else if let i = v as? Int { t = Double(i) }
        else if let s = v as? String, let f = ISO8601DateFormatter().date(from: s) { t = f.timeIntervalSince1970 }
        return t > 0 ? haceCuanto(t, ahora: Date().timeIntervalSince1970) : "nunca"
    }
}

struct HojaVecinos: View {
    @Binding var abierta: Hoja?
    @EnvironmentObject var motor: Motor
    @State private var e = EstadoMalla.leer()
    @State private var codigo = ""
    @State private var huella = ""
    @State private var pegado = ""
    let reloj = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

    var body: some View {
        HojaMarco(titulo: "Nodos vecinos", icono: "point.3.connected.trianglepath.dotted", abierta: $abierta) {
            Text("Otros nodos del MISMO evento en esta red (p. ej. un Google/Nest Wifi en todo el lugar) se encuentran solos y comparten su avance cada 30 s, sin internet. Lo de otros nodos queda de SÓLO LECTURA aquí; la nube sigue siendo la fuente final al subir.")
                .font(.system(size: 14)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if motor.estado?.malla?.activa != true {
                        Text("Se activa al encender el nodo (con un evento preparado).").font(.system(size: 14))
                    }
                    if e.sinPermisoRedLocal {
                        FilaHallazgo(h: Hallazgo(id: "redlocal", nivel: .problema, titulo: "macOS no deja a Nodo Camporee buscar en la red local",
                            guia: "Ajustes del Sistema › Privacidad y seguridad › Red local › activa «Nodo Camporee». (Si macOS preguntó «¿Permitir encontrar dispositivos en la red local?», pulsa Permitir.) Luego Apagar y Encender.",
                            detalle: "Bonjour (_nodocamporee._tcp) no contestó en 20 s."))
                    }
                    if e.vecinos.isEmpty && e.vistosBonjour.isEmpty {
                        Text("Todavía no hay vecinos. Si otra Mac del evento está en la misma red y no aparece: revisa «Interferencias» y el permiso de «Red local» (Ajustes del Sistema › Privacidad y seguridad › Red local › Nodo Camporee).")
                            .font(.system(size: 14))
                    }
                    ForEach(Array(e.vecinos.enumerated()), id: \.offset) { _, v in vecino(v) }
                    ForEach(Array(e.vistosBonjour.enumerated()), id: \.offset) { _, v in
                        let id = v["node_id"] as? String ?? ""
                        if !e.vecinos.contains(where: { ($0["node_id"] as? String) == id }) {
                            Tarjeta(luz: .gris, titulo: "«\(EstadoMalla.texto(v["nombre"]))» (encontrado)", detalle: "En \(EstadoMalla.texto(v["url"])) · esperando la primera sincronización")
                        }
                    }
                    if !e.rechazados.isEmpty {
                        Text("Rechazados").font(.system(size: 17, weight: .bold)).padding(.top, 6)
                        ForEach(Array(e.rechazados.enumerated()), id: \.offset) { _, r in
                            Tarjeta(luz: .rojo, titulo: EstadoMalla.texto(r["url"]), detalle: "\(EstadoMalla.texto(r["motivo"])) · \(EstadoMalla.cuando(r["cuando"]))")
                        }
                    }
                    Divider().padding(.vertical, 6)
                    emparejar
                }
            }
            if !motor.log.isEmpty { VistaLog(alto: 100) }
        }
        .onReceive(reloj) { _ in e = EstadoMalla.leer() }
    }

    func vecino(_ v: [String: Any]) -> some View {
        let traslapes = v["traslapes"] as? [[String: Any]] ?? []
        let error = EstadoMalla.texto(v["error"])
        let luz: Luz = !error.isEmpty ? .rojo : (traslapes.isEmpty ? .verde : .ambar)
        var det = "Captura: \(EstadoMalla.texto(v["actividades"])) · llegadas: \((v["llegadas"] as? Bool) == true ? "sí" : "no")"
        det += " · última sincronización \(EstadoMalla.cuando(v["ultima_sync"])) · \(EstadoMalla.texto(v["filas_recibidas"])) filas"
        det += " · \(EstadoMalla.texto(v["origen"]).isEmpty ? "" : "confianza: \(EstadoMalla.texto(v["origen"]))")"
        if !error.isEmpty { det += "\n\(error)" }
        if !traslapes.isEmpty {
            det += "\nTraslapes (NO se aplicaron): " + traslapes.prefix(5).map { "\(EstadoMalla.texto($0["tabla"])): \(EstadoMalla.texto($0["que"]))" }.joined(separator: "; ")
        }
        return Tarjeta(luz: luz, titulo: "«\(EstadoMalla.texto(v["label"]))»", detalle: det,
                       accion: ("Sincronizar ahora", { motor.correr(["malla", "tirar", EstadoMalla.texto(v["url"])], titulo: "Sincronizando con un vecino") }))
    }

    var emparejar: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Emparejar con otra Mac (si no se reconocen solas)").font(.system(size: 17, weight: .bold))
            Text("Sólo hace falta con un nodo preparado sin la nube (modo manual). Los preparados con «Conectar con adventist.club» se reconocen solos porque la nube los avala.")
                .font(.system(size: 13)).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Button("Mostrar el código de ESTA Mac") {
                        motor.correr(["malla", "emparejar-codigo"], titulo: "Código de emparejamiento") { _ in
                            codigo = (motor.ultimoJSON?["codigo"] as? String) ?? ""
                            huella = (motor.ultimoJSON?["huella"] as? String) ?? ""
                        }
                    }.disabled(motor.ocupado)
                    if !codigo.isEmpty {
                        if let img = imagenQR(codigo, tam: 600) {
                            Image(nsImage: img).interpolation(.none).resizable().frame(width: 200, height: 200)
                                .padding(8).background(Color.white)
                        }
                        Text("Huella: \(huella)").font(.system(size: 18, weight: .bold, design: .monospaced))
                        Button("Copiar el código") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(codigo, forType: .string) }
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("Código de la OTRA Mac (pégalo o escanéalo con un iPhone y AirDrop):").font(.system(size: 13))
                    TextEditor(text: $pegado).font(.system(size: 11, design: .monospaced)).frame(height: 70)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.gray.opacity(0.3)))
                    Text("Antes de aceptar, compara la HUELLA que muestra la otra Mac con la que aparece aquí al aceptar.").font(.system(size: 12)).foregroundStyle(.secondary)
                    Button("Aceptar a ese nodo") {
                        motor.correr(["malla", "emparejar-aceptar", pegado.trimmingCharacters(in: .whitespacesAndNewlines)], titulo: "Emparejando") { _ in e = EstadoMalla.leer() }
                    }.disabled(pegado.count < 20 || motor.ocupado)
                }
            }
        }
    }
}

// MARK: - Actualizaciones (en Ajustes)

struct SeccionActualizaciones: View {
    @EnvironmentObject var motor: Motor
    @ObservedObject var act = Actualizador.compartido
    @State private var imagenes: [String: Any] = [:]

    var version: String {
        let i = Bundle.main.infoDictionary ?? [:]
        return "\(i["CFBundleShortVersionString"] as? String ?? "?") (\(i["CFBundleVersion"] as? String ?? "?"), \(i["NodoCommit"] as? String ?? "?"))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Actualizaciones").font(.system(size: 18, weight: .bold))
            Text("Esta app: \(version). Revisa sola una vez al día si hay internet. Con el nodo ENCENDIDO nunca se actualiza: espera a «Apagar».")
                .font(.system(size: 13)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if !Actualizador.esPaquete {
                Text("Instalada desde el repo: se actualiza con instalar.sh (sin revisión automática).").font(.system(size: 13))
            }
            if act.esperandoApagar {
                Label("Hay una versión nueva lista: se instala al pulsar «Apagar».", systemImage: "arrow.down.app").font(.system(size: 14, weight: .semibold))
            }
            HStack {
                Button("Buscar actualizaciones") { act.buscar() }.disabled(!act.disponible)
                if let d = act.ultimaRevision { Text("Última revisión: \(d.formatted(date: .abbreviated, time: .shortened))").font(.system(size: 12)).foregroundStyle(.secondary) }
            }
            if imagenes["manifiesto"] as? Bool == true {
                let faltan = (imagenes["faltan"] as? Int) ?? 0
                let mb = ((imagenes["bytes"] as? Int) ?? 0) / 1_048_576
                HStack {
                    Text(faltan == 0 ? "Sistema (imágenes de Docker): al día, versión \(EstadoMalla.texto(imagenes["version"]))."
                         : "Faltan \(faltan) imagen(es) de esta versión (\(mb) MB).").font(.system(size: 13))
                    if faltan > 0 {
                        Button("Descargar imágenes") { motor.correr(["imagenes-actualizar"], titulo: "Descargando las imágenes del sistema") { _ in leerImagenes() } }
                            .disabled(motor.ocupado)
                    }
                }
            }
            if !act.ultimoAviso.isEmpty { Text(act.ultimoAviso).font(.system(size: 12)).foregroundStyle(.secondary) }
        }
        .onAppear { leerImagenes() }
    }

    func leerImagenes() {
        let ruta = motor.rutaMotor
        Task.detached {
            let d = Monitor.correrMotor(ruta, ["imagenes-estado"])
            let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] ?? [:]
            await MainActor.run { imagenes = o }
        }
    }
}
