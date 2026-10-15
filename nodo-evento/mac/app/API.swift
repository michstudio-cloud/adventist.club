// Conexión con adventist.club (modo automático). Los tokens viven SÓLO en memoria (`SesionAPI`):
// nunca se escriben en disco, en UserDefaults ni en registros, se borran al cerrar la hoja y a los
// 15 minutos, y al motor se le pasan sólo por el entorno (NODO_API_TOKEN).
import Foundation

// MARK: - Formas del contrato (CONTRATO_NODO_LOGIN.md)

struct Alcance: Codable, Equatable {
    var all: Bool
    var activity_ids: [String]
    var check_in: Bool
    init(all: Bool, activity_ids: [String], check_in: Bool) { self.all = all; self.activity_ids = activity_ids; self.check_in = check_in }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        all = (try? c.decode(Bool.self, forKey: .all)) ?? false
        activity_ids = (try? c.decode([String].self, forKey: .activity_ids)) ?? []
        check_in = (try? c.decode(Bool.self, forKey: .check_in)) ?? false
    }
}

/// Lo que dice `nodo-mac.sh nodo-info` de ESTE nodo (fila local). Tolera {"node": {...}} o plano.
struct InfoNodo: Decodable {
    var id: String?
    var label: String?
    var status: String?
    var all = false
    var check_in = false
    var activity_names: [String] = []
    var event_name: String?
    var last_upload_at: String?
    enum K: String, CodingKey { case node, node_id, id, label, status, scope, all, check_in, activity_names, event_name, event, last_upload_at }
    init(from d: Decoder) throws {
        var c = try d.container(keyedBy: K.self)
        if c.contains(.node) {
            if (try? c.decodeNil(forKey: .node)) == true { return }
            c = try c.nestedContainer(keyedBy: K.self, forKey: .node)
        }
        id = (try? c.decode(String.self, forKey: .node_id)) ?? (try? c.decode(String.self, forKey: .id))
        label = try? c.decode(String.self, forKey: .label)
        status = try? c.decode(String.self, forKey: .status)
        if let a = try? c.decode(Alcance.self, forKey: .scope) { all = a.all; check_in = a.check_in }
        if let v = try? c.decode(Bool.self, forKey: .all) { all = v }
        if let v = try? c.decode(Bool.self, forKey: .check_in) { check_in = v }
        activity_names = (try? c.decode([String].self, forKey: .activity_names)) ?? []
        event_name = (try? c.decode(String.self, forKey: .event_name)) ?? (try? c.decode(String.self, forKey: .event))
        last_upload_at = try? c.decode(String.self, forKey: .last_upload_at)
    }
    var preparado: Bool { label != nil || id != nil }
    var estadoTexto: String {
        switch status ?? "" {
        case "PREPARED": return "preparado"
        case "PARTIAL": return "subió avance"
        case "FINAL": return "subió final"
        case "CLOSED": return "cerrado por un admin"
        case "": return "preparado"
        default: return status!.lowercased()
        }
    }
    var capturaTexto: String { all ? "todas las actividades" : (activity_names.isEmpty ? "ninguna actividad" : activity_names.joined(separator: ", ")) }
}

struct ActividadAPI: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let kind: String?
    let parent_id: String?
    let position: Int?
}

struct NodoAPI: Decodable, Identifiable {
    let id: String
    let label: String
    let status: String
    let coordinator_email: String?
    let coordinator_name: String?
    let scope: Alcance?
    let activity_names: [String]?
    let prepared_at: String?
    let last_upload_at: String?
    let final_at: String?
    let uploads: Int?
    let conflicts: Int?
    let close_reason: String?
    var abierto: Bool { status == "PREPARED" || status == "PARTIAL" }
}

struct BloqueoAPI: Decodable {
    let locked_at: String?
    let locked_by_name: String?
    let reason: String?
    let node_label: String?
}

struct UltimaSyncAPI: Decodable {
    let kind: String?
    let created_at: String?
    let node_label: String?
    let actor_email: String?
}

struct EventoAPI: Decodable, Identifiable {
    let id: String
    let name: String
    let starts_on: String?
    let ends_on: String?
    let status: String?
    let venue: String?
    let city: String?
    let my_roles: [String]?
    let offline_lock: BloqueoAPI?
    let last_sync: UltimaSyncAPI?
    let activities: [ActividadAPI]?
    let nodes: [NodoAPI]?
    var actividades: [ActividadAPI] { activities ?? [] }
    var nodos: [NodoAPI] { nodes ?? [] }
    var fechas: String {
        guard let a = starts_on else { return "" }
        if let b = ends_on, b != a { return "\(a) – \(b)" }
        return a
    }
}

enum ErrorAPI: LocalizedError {
    case texto(String)
    case reLogin
    var errorDescription: String? {
        switch self {
        case .texto(let t): return t
        case .reLogin: return "La sesión venció: vuelve a iniciar sesión."
        }
    }
}

// MARK: - Sesión en memoria

@MainActor
final class SesionAPI: ObservableObject {
    /// Base de la API. Para pruebas locales: variable NODO_API_BASE o
    /// `defaults write club.adventist.nodo-camporee apiBasePruebas <url>` (se ve una etiqueta roja).
    static let basePorDefecto = "https://api.adventist.club/api/v1"
    static var base: String {
        if let e = ProcessInfo.processInfo.environment["NODO_API_BASE"], !e.isEmpty { return quitarBarra(e) }
        if let d = UserDefaults.standard.string(forKey: "apiBasePruebas"), !d.isEmpty { return quitarBarra(d) }
        return basePorDefecto
    }
    static var esPrueba: Bool { base != basePorDefecto }
    private static func quitarBarra(_ s: String) -> String { s.hasSuffix("/") ? String(s.dropLast()) : s }

    @Published private(set) var conectado = false
    @Published var correo = ""
    private var token: String?
    private var vence = Date.distantPast
    private var reloj: Timer?

    /// Sin caché, sin cookies, sin credenciales guardadas en disco.
    private let url: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 20
        c.timeoutIntervalForResource = 60
        c.httpCookieStorage = nil
        c.urlCache = nil
        c.httpAdditionalHeaders = ["User-Agent": "NodoCamporee-Mac/1"]
        return c.default()
    }()

    /// El token para el motor (sólo si sigue vigente).
    var tokenVigente: String? {
        guard let token, Date() < vence else { return nil }
        return token
    }

    func olvidar() {
        token = nil; vence = .distantPast; conectado = false
        reloj?.invalidate(); reloj = nil
    }

    private func guardarToken(_ t: String) {
        token = t
        vence = Date().addingTimeInterval(15 * 60)   // «sesión reciente» del servidor: 15 min
        conectado = true
        reloj?.invalidate()
        reloj = Timer.scheduledTimer(withTimeInterval: 15 * 60, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.olvidar() }
        }
    }

    private func pedir(_ metodo: String, _ ruta: String, cuerpo: [String: Any]? = nil, conToken: Bool = false) async throws -> (Data, HTTPURLResponse) {
        guard let u = URL(string: SesionAPI.base + ruta) else { throw ErrorAPI.texto("Dirección de la API inválida") }
        var r = URLRequest(url: u)
        r.httpMethod = metodo
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        if let cuerpo {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONSerialization.data(withJSONObject: cuerpo)
        }
        if conToken {
            guard let t = tokenVigente else { throw ErrorAPI.reLogin }
            r.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization")
        }
        do {
            let (d, resp) = try await url.data(for: r)
            guard let h = resp as? HTTPURLResponse else { throw ErrorAPI.texto("Respuesta inválida") }
            return (d, h)
        } catch let e as ErrorAPI { throw e }
        catch {
            throw ErrorAPI.texto("No hay conexión con adventist.club. Conéctate a una red con internet y prueba otra vez.")
        }
    }

    private static func detalle(_ d: Data, _ h: HTTPURLResponse) -> String {
        if let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            if let s = o["detail"] as? String { return s }
            if let a = o["detail"] as? [[String: Any]], let m = a.first?["msg"] as? String { return m }
            if let s = o["message"] as? String { return s }
        }
        return "Error \(h.statusCode)"
    }

    private static func traducir(_ s: String) -> String {
        let t: [String: String] = [
            "Incorrect email or password": "Correo o contraseña incorrectos.",
            "Invalid TOTP code": "El código no es correcto (o ya venció). Escribe el que muestra ahora tu app.",
            "Invalid recovery code": "El código de recuperación no es correcto.",
        ]
        return t[s] ?? s
    }

    /// Antes de iniciar sesión: ¿estamos hablando con la nube de verdad o con un nodo?
    func revisarRed() async throws {
        let (_, h) = try await pedir("GET", "/health")
        if h.value(forHTTPHeaderField: "X-Nodo-Local") == "1" {
            throw ErrorAPI.texto("Esta Mac está en la red del evento: «api.adventist.club» es un nodo, no internet. Conéctate a una red con internet real (casa, teléfono) y prueba otra vez.")
        }
        guard h.statusCode < 500 else { throw ErrorAPI.texto("adventist.club no responde bien ahora (\(h.statusCode)). Prueba en unos minutos.") }
    }

    enum ResultadoLogin { case listo, pideCodigo(temp: String) }

    func entrar(correo: String, clave: String) async throws -> ResultadoLogin {
        try await revisarRed()
        let (d, h) = try await pedir("POST", "/auth/login", cuerpo: ["email": correo, "password": clave])
        if h.statusCode == 429 {
            let s = h.value(forHTTPHeaderField: "Retry-After").flatMap(Int.init) ?? 60
            throw ErrorAPI.texto("Demasiados intentos. Espera \(s >= 60 ? "\(s / 60) min" : "\(s) s") y prueba otra vez.")
        }
        guard h.statusCode == 200 else { throw ErrorAPI.texto(SesionAPI.traducir(SesionAPI.detalle(d, h))) }
        let o = (try? JSONSerialization.jsonObject(with: d) as? [String: Any]) ?? [:]
        self.correo = correo
        if (o["mfa_required"] as? Bool) == true, let temp = o["temp_token"] as? String {
            return .pideCodigo(temp: temp)
        }
        guard let t = o["access_token"] as? String, !t.isEmpty else { throw ErrorAPI.texto("La respuesta no trae la sesión.") }
        guardarToken(t)
        return .listo
    }

    func verificar(temp: String, codigo: String) async throws {
        let c = codigo.trimmingCharacters(in: .whitespaces)
        let cuerpo: [String: Any] = c.count == 6 && c.allSatisfy(\.isNumber)
            ? ["temp_token": temp, "totp_code": c]
            : ["temp_token": temp, "recovery_code": c]
        let (d, h) = try await pedir("POST", "/auth/mfa/verify", cuerpo: cuerpo)
        if h.statusCode == 429 { throw ErrorAPI.texto("Demasiados intentos. Espera un minuto.") }
        guard h.statusCode == 200 else { throw ErrorAPI.texto(SesionAPI.traducir(SesionAPI.detalle(d, h))) }
        let o = (try? JSONSerialization.jsonObject(with: d) as? [String: Any]) ?? [:]
        guard let t = o["access_token"] as? String, !t.isEmpty else { throw ErrorAPI.texto("La respuesta no trae la sesión.") }
        guardarToken(t)
    }

    func misEventos() async throws -> [EventoAPI] {
        let (d, h) = try await pedir("GET", "/events/offline/mine", conToken: true)
        if h.statusCode == 401 { olvidar(); throw ErrorAPI.reLogin }
        guard h.statusCode == 200 else { throw ErrorAPI.texto(SesionAPI.detalle(d, h)) }
        do { return try JSONDecoder().decode([EventoAPI].self, from: d) }
        catch { throw ErrorAPI.texto("No entendí la lista de eventos (¿versión distinta de la API?).") }
    }
}

private extension URLSessionConfiguration {
    func `default`() -> URLSession { URLSession(configuration: self) }
}
