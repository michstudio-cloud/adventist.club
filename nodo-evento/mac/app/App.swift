// «Nodo Camporee»: convierte esta Mac en el nodo local del evento (README.md de esta carpeta).
import AppKit
import SwiftUI

@main
enum Entrada {
    static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        if let code = CLI.correr(args) { exit(code) }
        NodoApp.main()
    }
}

/// Modos sin ventana (para el LaunchAgent de respaldos, la instalación y las pruebas).
/// Nunca imprimen el valor de un secreto.
enum CLI {
    static func correr(_ args: [String]) -> Int32? {
        guard let primero = args.first else { return nil }
        switch primero {
        case "--dns":
            // DNS del nodo para «Mi red» (lo arranca el LaunchAgent «dns»; ver DNS.swift).
            return ServidorDNS.correr(Array(args.dropFirst()))
        case "--puerta":
            // Relevo :80/:443 → Caddy con la IP real de cada teléfono (LaunchAgent «puerta»; Puerta.swift).
            return Puerta.correr(Array(args.dropFirst()))
        case "--malla":
            // Bonjour de los nodos vecinos (LaunchAgent «malla»; Malla.swift).
            return MallaBonjour.correr(Array(args.dropFirst()))
        case "--motor":
            // Corre el motor con los secretos que esa orden necesita (desde el Llavero).
            let resto = Array(args.dropFirst())
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            let ruta = ProcessInfo.processInfo.environment["NODO_MOTOR"] ?? ((Bundle.main.resourcePath ?? ".") + "/nodo-mac.sh")
            p.arguments = [ruta] + resto
            if ["encender", "preparar-auto"].contains(resto.first ?? "") {
                for s in Secreto.allCases where s.generado { Llavero.generarSiFalta(s) }
            }
            p.environment = Motor.entorno(para: resto)
            do { try p.run() } catch { fputs("no pude correr el motor\n", stderr); return 1 }
            p.waitUntilExit()
            return p.terminationStatus
        case "--secreto-guardar":
            // El valor llega por la entrada estándar (no por argumentos: no aparece en `ps`).
            guard args.count == 2, let s = Secreto(rawValue: args[1]) else { fputs("uso: --secreto-guardar NOMBRE < valor\n", stderr); return 2 }
            let data = FileHandle.standardInput.readDataToEndOfFile()
            guard let v = String(data: data, encoding: .utf8), Llavero.guardar(s, v) else { fputs("no se guardó\n", stderr); return 1 }
            print("guardado en el Llavero: \(s.rawValue)")
            return 0
        case "--secreto-existe":
            guard args.count == 2, let s = Secreto(rawValue: args[1]) else { return 2 }
            let ok = Llavero.existe(s)
            print("\(s.rawValue): \(ok ? "sí" : "no")")
            return ok ? 0 : 1
        case "--secreto-borrar":
            guard args.count == 2, let s = Secreto(rawValue: args[1]) else { return 2 }
            Llavero.borrar(s)
            print("borrado del Llavero: \(s.rawValue)")
            return 0
        case "--secreto-prueba":
            // Guarda, lee y compara un valor de prueba sin mostrarlo; luego lo borra.
            let s = Secreto.wifi
            let previo = Llavero.leer(s)
            var bytes = [UInt8](repeating: 0, count: 16)
            _ = SecRandomCopyBytes(kSecRandomDefault, 16, &bytes)
            let valor = "prueba-" + bytes.map { String(format: "%02x", $0) }.joined()
            let guardado = Llavero.guardar(s, valor)
            let leido = Llavero.leer(s)
            let igual = (leido == valor)
            if let previo { Llavero.guardar(s, previo) } else { Llavero.borrar(s) }
            print("Llavero: guardar=\(guardado) leer=\(leido != nil) coincide=\(igual) restaurado=\(previo == nil ? !Llavero.existe(s) : Llavero.leer(s) == previo)")
            return (guardado && igual) ? 0 : 1
        default:
            return nil
        }
    }
}

final class Delegado: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.activate(ignoringOtherApps: true)
        if CommandLine.arguments.contains("--prueba") { Prueba.activar() }
        Actualizador.compartido.iniciar()
    }
}

struct NodoApp: App {
    @NSApplicationDelegateAdaptor(Delegado.self) var delegado
    @StateObject private var motor = Motor()

    var body: some Scene {
        Window(Bundle.main.bundleIdentifier == "club.adventist.nodo-camporee" ? "Nodo Camporee" : "PRUEBA — no usar · Nodo Camporee", id: "principal") {
            Principal()
                .environmentObject(motor)
                .frame(minWidth: 1040, minHeight: 760)
                .onAppear {
                    motor.arrancarSondeo()
                    if CommandLine.arguments.contains("--encender") {
                        // Auto-arranque al iniciar sesión (LaunchAgent opcional).
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                            motor.correr(["encender"], titulo: "Encendiendo el nodo")
                        }
                    }
                }
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
                Button("Buscar actualizaciones…") { Actualizador.compartido.buscar() }
            }
        }
    }
}

/// ¿La app corre desde el DMG o desde una copia aislada por macOS (App Translocation)? Entonces
/// no debe instalar: las rutas cambian en cada apertura. Hay que arrastrarla a Aplicaciones.
enum Ubicacion {
    static var provisional: Bool {
        let p = Bundle.main.bundlePath
        return p.contains("/AppTranslocation/") || p.hasPrefix("/Volumes/")
    }
}

/// Modo de prueba (sólo con `--prueba`): un script puede abrir hojas, pulsar Encender/Apagar y
/// guardar capturas de la ventana sin permisos de grabación de pantalla.
///   aviso: club.adventist.nodo-camporee.prueba  {"accion": "hoja:qr" | "hoja:conectar" | "hoja:subir" |
///          "hoja:red" | "red:glinet|google|mi-red|otro|lugar|varios|elegir" | "modo:auto|manual" |
///          "auto:entrar:correo|clave" | "auto:codigo:123456" | "auto:evento:N" | "auto:actividad:N" |
///          "auto:todas" | "auto:preparar" | "auto:final" | "auto:revisar" | "auto:subir" |
///          "otro:mikrotik|openwrt|manual|simular" | "cerrar" | "instalar" | "encender" | "apagar" |
///          "motor:<orden>" | "captura:/ruta/nombre"}
enum Prueba {
    static let interno = Notification.Name("nodo.prueba")
    /// «red:<opción>» con la hoja de red cerrada: se abre y entra directo a esa opción.
    static var redPendiente: String?
    static func activar() {
        DistributedNotificationCenter.default().addObserver(forName: Notification.Name("club.adventist.nodo-camporee.prueba"),
                                                            object: nil, queue: .main) { n in
            guard let accion = n.object as? String else { return }
            if accion.hasPrefix("captura:") { capturar(String(accion.dropFirst(8))) }
            else { NotificationCenter.default.post(name: interno, object: accion) }
        }
    }
    static func capturar(_ base: String) {
        for w in NSApp.windows where w.isVisible && w.sheetParent == nil {
            guard let v = w.contentView?.superview ?? w.contentView else { continue }
            guardar(v, "\(base).png")
            if let hoja = w.attachedSheet, let hv = hoja.contentView?.superview ?? hoja.contentView {
                guardar(hv, "\(base)-hoja.png")
            }
        }
    }
    static func guardar(_ v: NSView, _ ruta: String) {
        guard let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return }
        v.cacheDisplay(in: v.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: ruta))
    }
}
