// Secretos del nodo en el Llavero de macOS (nunca en archivos ni en registros).
import Foundation
import Security

enum Secreto: String, CaseIterable, Identifiable {
    // Los pide la app una vez (los tiene el propietario).
    case eventPass = "EVENT_PASS_SECRET"
    case prodURL = "PROD_DATABASE_URL"
    case cloudflare = "CF_DNS_API_TOKEN"
    case wifi = "WIFI_PASSWORD"
    // Propios del nodo: la app los genera la primera vez.
    case jwt = "JWT_SECRET"
    case proxy = "PROXY_SHARED_SECRET"
    case postgres = "POSTGRES_PASSWORD"

    var id: String { rawValue }
    var generado: Bool { [.jwt, .proxy, .postgres].contains(self) }

    var titulo: String {
        switch self {
        case .eventPass: return "Secreto de los pases"
        case .prodURL: return "Conexión de producción"
        case .cloudflare: return "Token de Cloudflare"
        case .wifi: return "Contraseña del Wi-Fi del evento"
        case .jwt: return "Firma de sesiones del nodo"
        case .proxy: return "Secreto interno web↔API"
        case .postgres: return "Contraseña de la base del nodo"
        }
    }

    var ayuda: String {
        switch self {
        case .eventPass:
            return "EVENT_PASS_SECRET: el MISMO valor que tiene producción (Render › adventist-club-api › Environment). Con él se leen los pases impresos. Lo guarda el propietario en su gestor de contraseñas."
        case .prodURL:
            return "Cadena de conexión de la base de producción (postgresql://…). Sólo se usa al «Preparar evento» y al «Subir resultados»."
        case .cloudflare:
            return "Token de API de Cloudflare con permiso «Zone · DNS · Edit» sólo para adventist.club. Sólo para sacar o renovar el certificado (con internet)."
        case .wifi:
            return "Opcional. Contraseña del Wi-Fi del GL.iNet: se usa al configurar el router y para el QR «Conéctate al Wi-Fi»."
        default:
            return "Lo genera la app automáticamente; no hace falta tocarlo."
        }
    }
}

/// Todo pasa por /usr/bin/security (firmado por Apple): así el acceso a los secretos no depende de
/// la firma de esta app y sobrevive a reinstalaciones sin que macOS vuelva a preguntar. Los valores
/// viajan por tuberías (stdin/stdout), nunca como argumentos (no aparecen en `ps`).
enum Llavero {
    // NODO_LLAVERO_SERVICIO: sólo pruebas (secretos aparte de los reales de esta Mac).
    static let servicio = ProcessInfo.processInfo.environment["NODO_LLAVERO_SERVICIO"] ?? "club.adventist.nodo-camporee"

    @discardableResult
    private static func security(_ args: [String], entrada: String? = nil) -> (Int32, Data) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        p.arguments = args
        let out = Pipe(); p.standardOutput = out; p.standardError = FileHandle.nullDevice
        let inp = Pipe(); p.standardInput = entrada == nil ? FileHandle.nullDevice : inp
        do { try p.run() } catch { return (-1, Data()) }
        if let entrada {
            inp.fileHandleForWriting.write(Data(entrada.utf8))
            try? inp.fileHandleForWriting.close()
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, data)
    }

    static func leer(_ s: Secreto) -> String? {
        let (rc, data) = security(["find-generic-password", "-s", servicio, "-a", s.rawValue, "-w"])
        guard rc == 0, let v = String(data: data, encoding: .utf8) else { return nil }
        let t = v.trimmingCharacters(in: .newlines)
        return t.isEmpty ? nil : t
    }

    static func existe(_ s: Secreto) -> Bool {
        security(["find-generic-password", "-s", servicio, "-a", s.rawValue]).0 == 0
    }

    @discardableResult
    static func guardar(_ s: Secreto, _ valor: String) -> Bool {
        let v = valor.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !v.isEmpty, !v.contains("\n") else { return false }
        borrar(s)
        // `-w` al final: security pide el valor (y su confirmación) por la entrada estándar.
        let (rc, _) = security(["add-generic-password", "-s", servicio, "-a", s.rawValue,
                                "-l", "Nodo Camporee · \(s.rawValue)", "-w"], entrada: v + "\n" + v + "\n")
        return rc == 0 && leer(s) == v
    }

    @discardableResult
    static func borrar(_ s: Secreto) -> Bool {
        let rc = security(["delete-generic-password", "-s", servicio, "-a", s.rawValue]).0
        return rc == 0 || rc == 44
    }

    /// 32 bytes aleatorios en hex (como `openssl rand -hex 32`).
    static func generarSiFalta(_ s: Secreto) {
        guard s.generado, !existe(s) else { return }
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { return }
        guardar(s, bytes.map { String(format: "%02x", $0) }.joined())
    }
}
