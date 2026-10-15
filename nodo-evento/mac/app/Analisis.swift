// Modelo común de los analizadores (Interferencias, SeguridadMac, Dispositivos) y un ejecutor de
// órdenes de sólo lectura que nunca se queda colgado.
//
// Los analizadores son «puros»: reciben el texto de las órdenes (Muestras) y devuelven hallazgos.
// Así se prueban con salidas capturadas (pruebas/muestras) sin tocar la Mac. `recolectar` sólo
// corre órdenes de Apple de sólo lectura: nunca cambia un ajuste ni pide contraseña.
import Foundation

/// ok = verde · info = gris · aviso = ámbar · problema = rojo
enum Nivel: String, Codable { case ok, info, aviso, problema }

struct Hallazgo: Identifiable, Codable, Equatable {
    let id: String
    let nivel: Nivel
    let titulo: String
    /// Qué hacer, en palabras sencillas (pasos en Ajustes del Sistema).
    let guia: String
    /// Datos técnicos (para quien ayuda por teléfono). Sin secretos ni nombres de usuario: pasa
    /// siempre por el Depurador (se puede seleccionar y copiar en la ventana).
    let detalle: String

    init(id: String, nivel: Nivel, titulo: String, guia: String, detalle: String) {
        self.id = id; self.nivel = nivel; self.titulo = titulo; self.guia = guia
        self.detalle = Depurador.texto(detalle)
    }
}

enum Comando {
    /// Corre una orden y devuelve stdout+stderr. Nunca se cuelga: a los `timeout` segundos la
    /// termina (SIGTERM y luego SIGKILL). Si no existe o falla al arrancar, devuelve "".
    /// La salida va a un archivo temporal (no a una tubería) para que un proceso nieto que se
    /// quede con la tubería abierta no bloquee la lectura. Idioma forzado a inglés (LC_ALL=C):
    /// los analizadores reconocen los textos en inglés de las herramientas de Apple.
    static func correr(_ path: String, _ args: [String], timeout: Double = 5) -> String {
        guard FileManager.default.isExecutableFile(atPath: path) else { return "" }
        guard permitido(path, args) else { return "" }
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("nodo-cmd-\(UUID().uuidString).txt")
        guard FileManager.default.createFile(atPath: tmp.path, contents: nil),
              let fh = try? FileHandle(forWritingTo: tmp) else { return "" }
        defer { try? fh.close(); try? FileManager.default.removeItem(at: tmp) }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["LC_ALL"] = "C"; env["LANG"] = "C"
        p.environment = env
        p.standardOutput = fh; p.standardError = fh
        p.standardInput = FileHandle.nullDevice
        let listo = DispatchSemaphore(value: 0)
        p.terminationHandler = { _ in listo.signal() }
        do { try p.run() } catch { return "" }
        if listo.wait(timeout: .now() + timeout) == .timedOut {
            p.terminate()
            if listo.wait(timeout: .now() + 0.5) == .timedOut {
                kill(p.processIdentifier, SIGKILL)
                _ = listo.wait(timeout: .now() + 0.5)
            }
        }
        try? fh.synchronize()
        let datos = (try? Data(contentsOf: tmp)) ?? Data()
        return String(decoding: datos, as: UTF8.self)
    }
}

extension Comando {
    /// `ps` sólo con columnas sin argumentos (comm, pid, ppid, user…): los argumentos de otros
    /// programas pueden traer llaves (incidente oct 2026: `--header X-Goog-Api-Key: …` de una
    /// muestra de pruebas). `args`, `command`, `-f`/`-ww`… no se piden nunca.
    static let columnasPS: Set<String> = ["comm", "ucomm", "pid", "ppid", "user", "uid", "state", "stat", "%cpu", "%mem", "rss", "etime"]
    static func permitido(_ path: String, _ args: [String]) -> Bool {
        guard (path as NSString).lastPathComponent == "ps" else { return true }
        var i = 0, columnas = false
        while i < args.count {
            let a = args[i]
            if a.hasPrefix("-") && a.contains("o") && !a.hasPrefix("--") {
                // -o / -axo / -eo: la lista de columnas va en el siguiente argumento.
                guard i + 1 < args.count else { return false }
                let lista = args[i + 1].split(separator: ",").map { String($0.split(separator: "=").first ?? "").lowercased() }
                guard !lista.isEmpty, lista.allSatisfy({ columnasPS.contains($0) }) else { return false }
                guard a.dropFirst().allSatisfy({ "axeo".contains($0) }) else { return false }
                columnas = true; i += 2; continue
            }
            return false   // cualquier otra opción (-f, -l, -ww, -j, -u…) puede mostrar argumentos
        }
        return columnas
    }
}

/// Utilidades de red compartidas por los analizadores (prefijo propio para no chocar con la app).
enum AnalisisIP {
    static func octetos(_ ip: String) -> [Int]? {
        let p = ip.split(separator: ".", omittingEmptySubsequences: false)
        guard p.count == 4 else { return nil }
        let n = p.compactMap { Int($0) }
        guard n.count == 4, n.allSatisfy({ (0...255).contains($0) }) else { return nil }
        return n
    }
    static func esIPv4(_ ip: String) -> Bool { octetos(ip) != nil }
    /// 100.64.0.0/10 (CGNAT: Tailscale, Meshnet de NordVPN).
    static func esCGNAT(_ ip: String) -> Bool {
        guard let o = octetos(ip) else { return false }
        return o[0] == 100 && (64...127).contains(o[1])
    }
    /// 10/8, 172.16/12, 192.168/16.
    static func esPrivadaV4(_ ip: String) -> Bool {
        guard let o = octetos(ip) else { return false }
        return o[0] == 10 || (o[0] == 172 && (16...31).contains(o[1])) || (o[0] == 192 && o[1] == 168)
    }
    static func esLoopback(_ ip: String) -> Bool {
        ip == "::1" || (octetos(ip)?.first == 127)
    }
    static func esLinkLocalV6(_ ip: String) -> Bool { ip.lowercased().hasPrefix("fe80:") }
    /// Quita «%en0» de las IPv6 con zona.
    static func sinZona(_ ip: String) -> String { String(ip.split(separator: "%").first ?? Substring(ip)) }
}
