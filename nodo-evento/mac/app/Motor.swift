// Puente con el motor (nodo-mac.sh): corre órdenes, pasa secretos por el entorno, lee el estado.
import Foundation
import SwiftUI

struct Estado: Decodable {
    struct Servicios: Decodable { let db, api, web, caddy, respaldo: String }
    struct Red: Decodable {
        let interfaz, tipo, ip, mac: String
        let en_red_router, router_responde, glinet: Bool
        let ip_fija: String
        let ip_cambio: Bool
        let router: String?
        let gateway: String?
        let mac_hw: String?
        let mac_privada: Bool?
    }
    struct DNS: Decodable { let activo, responde: Bool; let interceptado: Bool?; let puerto: Int }
    struct Cert: Decodable {
        let existe: Bool; let tipo, vence: String; let dias: Int?; let cubre_evento: Bool; let nombres: String
    }
    struct Respaldo: Decodable {
        let local_dir, ultimo: String; let ultimo_t: Double
        let usb: Bool; let usb_dir, usb_ultimo: String; let usb_ultimo_t: Double
        let espejo_activo: Bool; let ultimo_espejo: String; let cada_min: Int
    }
    struct Telefonos: Decodable { let ips_10min, peticiones_10min: Int }
    struct Energia: Decodable { let bateria: Bool; let porcentaje: Int?; let despierto: Bool }
    /// Versión portátil (DMG): si falta «Instalar» y si «Construir» tiene sentido (repos presentes).
    struct Instalacion: Decodable {
        let paquete, pendiente: Bool
        let motivo, version_paquete, version_instalada, herramientas: String
        let puede_construir: Bool
    }

    let ahora: Double
    let docker, colima, imagenes: Bool
    let encendido_desde: String
    let servicios: Servicios
    let https_local, https_lan, web_ok, api_ok: Bool
    let red: Red
    let certificado: Cert
    let respaldo: Respaldo
    let telefonos: Telefonos
    let energia: Energia
    let disco_libre_gb: Int
    let firewall, autoarranque: Bool
    let instalacion: Instalacion?
    let dns: DNS?
    let nodo: InfoNodo?
    // v3
    struct Puerta: Decodable { let activa: Bool; let configurada: Bool? }
    struct Malla: Decodable { let activa: Bool }
    let puerta: Puerta?
    let malla: Malla?
    let config: [String: String]

    var nodoArriba: Bool { ["healthy", "running"].contains(servicios.caddy) && ["healthy"].contains(servicios.db) }
    var nodoSano: Bool { nodoArriba && web_ok && api_ok }
}

struct LineaLog: Identifiable {
    enum Tipo { case texto, paso, ok, aviso, error }
    let id = UUID()
    let tipo: Tipo
    let texto: String
}

@MainActor
final class Motor: ObservableObject {
    @Published var estado: Estado?
    @Published var ocupado = false
    @Published var tarea = ""
    @Published var paso = ""
    @Published var progreso: Double = 0
    @Published var log: [LineaLog] = []
    @Published var ultimoCodigo: Int32 = 0
    @Published var conflictos = false
    @Published var secretosPresentes: [Secreto: Bool] = [:]
    /// Última línea «@@json …» de la orden en curso (resumen de preparar/subir/revisar).
    @Published var ultimoJSON: [String: Any]? = nil
    /// La orden pidió volver a iniciar sesión (código 4 / @@relogin).
    @Published var pideLogin = false

    let rutaMotor: String
    private var timer: Timer?
    private var leyendoEstado = false

    init() {
        if let env = ProcessInfo.processInfo.environment["NODO_MOTOR"] { rutaMotor = env }
        else { rutaMotor = (Bundle.main.resourcePath ?? ".") + "/nodo-mac.sh" }
        refrescarSecretos()
    }

    func arrancarSondeo() {
        refrescarEstado()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refrescarEstado() }
        }
    }

    func refrescarSecretos() {
        var m: [Secreto: Bool] = [:]
        for s in Secreto.allCases { m[s] = Llavero.existe(s) }
        secretosPresentes = m
    }

    func cfg(_ k: String) -> String { estado?.config[k] ?? "" }
    /// Modo automático (por defecto) o manual; se recuerda en config.env.
    var modoManual: Bool { cfg("MODO") == "manual" }

    // Qué secretos necesita cada orden (sólo esos viajan en el entorno).
    nonisolated static func secretosPara(_ orden: String) -> [Secreto] {
        let base: [Secreto] = [.postgres, .jwt, .proxy, .eventPass, .operador]
        switch orden {
        case "encender", "apagar", "respaldar", "construir", "preparar-auto", "subir-auto", "nodo-info": return base
        case "exportar", "importar", "sync-estado": return base + [.prodURL, .prodEventPass]
        case "cert-letsencrypt": return [.cloudflare]
        case "router-plan", "router-aplicar": return [.wifi]
        case "dispositivos", "cuentas", "malla", "caddy-recargar", "imagenes-actualizar", "imagenes-estado": return base
        case "red-publica-plan", "red-publica-aplicar", "red-publica-quitar": return base + [.wifi]
        default: return []
        }
    }

    nonisolated static func entorno(para args: [String], extra: [String: String] = [:]) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["NODO_APP_BUNDLE"] = Bundle.main.bundlePath
        for s in secretosPara(args.first ?? "") {
            if let v = Llavero.leer(s) { env[s.rawValue] = v }
        }
        for (k, v) in extra { env[k] = v }
        return env
    }

    func refrescarEstado() {
        guard !leyendoEstado else { return }
        leyendoEstado = true
        let ruta = rutaMotor
        Task.detached {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = [ruta, "estado"]
            p.environment = Motor.entorno(para: ["estado"])
            let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
            var nuevo: Estado?
            do {
                try p.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                nuevo = try? JSONDecoder().decode(Estado.self, from: data)
            } catch {}
            let leido = nuevo
            await MainActor.run {
                if let leido { self.estado = leido }
                self.leyendoEstado = false
            }
        }
    }

    /// Corre una orden del motor mostrando el progreso. `extra`: valores sólo en memoria
    /// (p. ej. la contraseña del router), nunca guardados.
    func correr(_ args: [String], titulo: String, extra: [String: String] = [:], limpiar: Bool = true,
                alTerminar: ((Int32) -> Void)? = nil) {
        guard !ocupado else { return }
        if ["encender", "preparar-auto"].contains(args.first ?? "") { prepararSecretosDelNodo() }
        ocupado = true; tarea = titulo; paso = ""; progreso = 0; conflictos = false
        ultimoJSON = nil; pideLogin = false
        if limpiar { log = [] }
        let ruta = rutaMotor
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [ruta] + args
        p.environment = Motor.entorno(para: args, extra: extra)
        p.standardInput = FileHandle.nullDevice
        let pipe = Pipe()
        p.standardOutput = pipe; p.standardError = pipe
        var resto = ""
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            Task { @MainActor in
                guard let self else { return }
                resto += s
                while let r = resto.range(of: "\n") {
                    let linea = String(resto[..<r.lowerBound]); resto = String(resto[r.upperBound...])
                    self.agregar(linea)
                }
            }
        }
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor in
                guard let self else { return }
                try? await Task.sleep(nanoseconds: 200_000_000)
                pipe.fileHandleForReading.readabilityHandler = nil
                if !resto.isEmpty { self.agregar(resto); resto = "" }
                self.ultimoCodigo = code
                if code == 4 { self.pideLogin = true }
                self.ocupado = false
                if code == 0 { self.progreso = 1 }
                self.refrescarEstado()
                // Una actualización que esperaba a «Apagar» se instala ahora.
                if args.first == "apagar" && code == 0 { Actualizador.compartido.nodoApagado() }
                alTerminar?(code)
            }
        }
        do { try p.run() } catch {
            agregar("@@error No pude correr el motor: \(error.localizedDescription)")
            ocupado = false
        }
    }

    private func agregar(_ linea: String) {
        if linea.hasPrefix("@@paso ") {
            let t = String(linea.dropFirst(7))
            if let sp = t.firstIndex(of: " ") {
                let frac = t[..<sp].split(separator: "/")
                if frac.count == 2, let a = Double(frac[0]), let b = Double(frac[1]), b > 0 { progreso = (a - 1) / b }
                paso = String(t[t.index(after: sp)...])
            } else { paso = t }
            log.append(LineaLog(tipo: .paso, texto: paso))
        } else if linea.hasPrefix("@@ok ") {
            log.append(LineaLog(tipo: .ok, texto: String(linea.dropFirst(5))))
        } else if linea.hasPrefix("@@aviso ") {
            log.append(LineaLog(tipo: .aviso, texto: String(linea.dropFirst(8))))
        } else if linea.hasPrefix("@@error ") {
            log.append(LineaLog(tipo: .error, texto: String(linea.dropFirst(8))))
        } else if linea == "@@conflictos" {
            conflictos = true
        } else if linea == "@@relogin" {
            pideLogin = true
        } else if linea.hasPrefix("@@json ") {
            if let d = String(linea.dropFirst(7)).data(using: .utf8),
               let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { ultimoJSON = o }
        } else {
            log.append(LineaLog(tipo: .texto, texto: linea))
        }
        if log.count > 2000 { log.removeFirst(log.count - 2000) }
    }

    func prepararSecretosDelNodo() {
        for s in Secreto.allCases where s.generado { Llavero.generarSiFalta(s) }
        refrescarSecretos()
    }

    /// Orden corta sin progreso ni registro (p. ej. «red-modo»); refresca el estado al terminar.
    func silencioso(_ args: [String]) {
        let ruta = rutaMotor
        Task.detached {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = [ruta] + args
            p.environment = Motor.entorno(para: args)
            p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
            try? p.run(); p.waitUntilExit()
            await MainActor.run { self.refrescarEstado() }
        }
    }

    func guardarConfig(_ clave: String, _ valor: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [rutaMotor, "config", "set", clave, valor]
        p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
        try? p.run(); p.waitUntilExit()
        refrescarEstado()
    }
}
