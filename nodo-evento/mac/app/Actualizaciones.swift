// Actualizaciones de la app con Sparkle 2 (MIT, https://sparkle-project.org).
//
// Reglas del nodo:
//   • Revisión automática una vez al día (SUScheduledCheckInterval) cuando hay internet; sin
//     internet Sparkle falla en silencio.
//   • NUNCA se actualiza con el nodo ENCENDIDO (hay un evento en curso): la revisión automática ni
//     siquiera corre, y si alguien pulsa «Buscar actualizaciones» e «Instalar», la instalación
//     espera hasta «Apagar» (o hasta la próxima vez que se abra la app con el nodo apagado).
//   • Actualizaciones chicas: el archivo de la actualización es SÓLO la app (sin las imágenes de
//     Docker ni Colima). Las imágenes van aparte, por versión (imagenes.json dentro de la app) y sólo
//     se descargan si cambiaron. El DMG sigue siendo el instalador completo sin internet.
//   • Cada actualización va firmada (EdDSA, SUPublicEDKey en Info.plist) y con el Developer ID.
//   • Instalada desde el repo (instalar.sh): sin revisión automática (se actualiza con instalar.sh).
import AppKit
import Foundation
import Sparkle

@MainActor
final class Actualizador: NSObject, ObservableObject, SPUUpdaterDelegate {
    static let compartido = Actualizador()

    @Published var esperandoApagar = false
    @Published var ultimoAviso = ""
    private var controlador: SPUStandardUpdaterController?
    private var instalarDespues: (() -> Void)?

    nonisolated static var carpetaRun: String {
        let base = ProcessInfo.processInfo.environment["NODO_APP_SUPPORT"]
            ?? (NSHomeDirectory() + "/Library/Application Support/Nodo Camporee")
        return base + "/run"
    }
    /// El motor deja run/encendido mientras el nodo está encendido (lo borra «Apagar»).
    nonisolated static var nodoEncendido: Bool { FileManager.default.fileExists(atPath: carpetaRun + "/encendido") }
    /// Versión portátil (DMG / actualización): trae paquete.env. Instalada desde el repo: no.
    static var esPaquete: Bool { FileManager.default.fileExists(atPath: (Bundle.main.resourcePath ?? "") + "/paquete.env") }
    static var enPruebas: Bool { CommandLine.arguments.contains("--prueba") }
    var disponible: Bool { controlador != nil }

    func iniciar() {
        guard controlador == nil, Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") != nil else { return }
        // Desde el DMG montado o una copia aislada por macOS no se actualiza (no se puede reemplazar).
        if Ubicacion.provisional && !Actualizador.enPruebas { return }
        let c = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controlador = c
        if !Actualizador.esPaquete && !Actualizador.enPruebas {
            c.updater.automaticallyChecksForUpdates = false
        }
        if Actualizador.enPruebas, UserDefaults.standard.bool(forKey: "actualizarSoloPruebas") {
            // Prueba automática: baja e instala al salir, sin ventanas.
            c.updater.automaticallyDownloadsUpdates = true
        }
        c.startUpdater()
        Actualizador.registrar("iniciado: feed \(c.updater.feedURL?.absoluteString ?? "-") · automático \(c.updater.automaticallyChecksForUpdates) · descarga sola \(c.updater.automaticallyDownloadsUpdates)")
    }

    /// «Buscar actualizaciones» (botón o menú).
    func buscar() {
        guard let c = controlador else { return }
        c.checkForUpdates(nil)
    }

    /// Sólo pruebas: revisa en segundo plano (con `actualizarSoloPruebas` baja e instala al salir).
    func buscarEnSegundoPlano() { controlador?.updater.checkForUpdatesInBackground() }

    /// El motor apagó el nodo: si había una instalación esperando, ahora sí.
    func nodoApagado() {
        guard !Actualizador.nodoEncendido, let h = instalarDespues else { return }
        instalarDespues = nil
        esperandoApagar = false
        h()
    }

    var ultimaRevision: Date? { controlador?.updater.lastUpdateCheckDate }

    /// Bitácora corta en run/actualizaciones.log (sin secretos): qué se buscó, qué se encontró.
    nonisolated static func registrar(_ s: String) {
        let ruta = carpetaRun + "/actualizaciones.log"
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let linea = "\(f.string(from: Date())) \(s)\n"
        if !FileManager.default.fileExists(atPath: ruta) { FileManager.default.createFile(atPath: ruta, contents: nil) }
        if let h = FileHandle(forWritingAtPath: ruta) { h.seekToEndOfFile(); h.write(Data(linea.utf8)); try? h.close() }
    }

    nonisolated func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        Actualizador.registrar("encontrada: \(item.displayVersionString) (\(item.versionString))")
    }
    nonisolated func updaterDidNotFindUpdate(_ updater: SPUUpdater) { Actualizador.registrar("sin actualizaciones") }
    nonisolated func updater(_ updater: SPUUpdater, didDownloadUpdate item: SUAppcastItem) { Actualizador.registrar("descargada: \(item.versionString)") }
    nonisolated func updater(_ updater: SPUUpdater, didAbortWithError error: any Error) { Actualizador.registrar("error: \((error as NSError).localizedDescription)") }
    nonisolated func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) { Actualizador.registrar("instalando: \(item.versionString)") }

    // MARK: SPUUpdaterDelegate

    nonisolated func feedURLString(for updater: SPUUpdater) -> String? {
        // Pruebas: un appcast local (carpeta temporal servida en 127.0.0.1). Nunca en uso normal.
        guard CommandLine.arguments.contains("--prueba") else { return nil }
        return UserDefaults.standard.string(forKey: "feedPruebas")
    }

    nonisolated func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        // Con el nodo encendido no se revisa en segundo plano (ni ventanas ni descargas durante el evento).
        if updateCheck == .updatesInBackground && Actualizador.nodoEncendido {
            throw NSError(domain: "club.adventist.nodo-camporee", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "El nodo está encendido: las actualizaciones esperan a «Apagar»."])
        }
    }

    nonisolated func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
                             untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        guard Actualizador.nodoEncendido else { return false }
        Task { @MainActor in
            self.instalarDespues = installHandler
            self.esperandoApagar = true
            self.ultimoAviso = "La versión \(item.displayVersionString) se instalará al pulsar «Apagar»."
        }
        return true
    }

    nonisolated func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem,
                             immediateInstallationBlock: @escaping () -> Void) -> Bool {
        // Instalar al salir (sólo con descarga automática, es decir, en pruebas): nunca con el nodo
        // encendido. Devolver true = «yo me encargo»: se instalará al apagar.
        Actualizador.registrar("lista para instalar al salir: \(item.versionString)\(Actualizador.nodoEncendido ? " (espera a Apagar)" : "")")
        if Actualizador.nodoEncendido {
            Task { @MainActor in
                self.instalarDespues = immediateInstallationBlock
                self.esperandoApagar = true
            }
            return true
        }
        return false
    }

    nonisolated func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        let texto = error.map { ($0 as NSError).localizedDescription } ?? ""
        Actualizador.registrar("ciclo terminado (\(updateCheck.rawValue))\(texto.isEmpty ? "" : ": " + texto)")
        let aviso = Actualizador.avisoPara(error)
        Task { @MainActor in self.ultimoAviso = aviso }
    }

    /// Texto para Ajustes (en español; los errores de Sparkle vienen en el idioma del sistema).
    /// Códigos de SUErrors.h: 1001 = sin actualizaciones, 1000/1002 = no se pudo leer el appcast
    /// (sin internet, o todavía no hay nada publicado en el servidor), 2001 = descarga fallida,
    /// 3001+ = firma o instalación.
    nonisolated static func avisoPara(_ error: (any Error)?) -> String {
        guard let e = error as NSError? else { return "" }
        if e.domain == "club.adventist.nodo-camporee" { return e.localizedDescription }
        guard e.domain == SUSparkleErrorDomain else { return e.localizedDescription }
        switch e.code {
        case 1001: return "Al día: no hay una versión más nueva."
        case 1000, 1002: return "No se pudo revisar: sin internet o todavía no hay actualizaciones publicadas."
        case 2001: return "No se pudo descargar la actualización (¿internet?). Se intentará otra vez."
        case 3000..<5000: return "La actualización no pasó la revisión de firma o no se pudo instalar; no se cambió nada."
        default: return e.localizedDescription
        }
    }
}
