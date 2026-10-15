// Depurador de textos de la Mac (docs/EGRESO.md). Todo texto que viene de órdenes del sistema
// (ps, netstat, scutil, ifconfig, arp, launchctl, systemextensionsctl, el router, los registros de
// Caddy/API) pasa por aquí ANTES de mostrarse, copiarse o guardarse: quita llaves y tokens, pares
// KEY=valor, banderas --token/--password, credenciales en URLs, bloques PEM privados, base64 largo
// junto a key/token/secret, contraseñas del Wi-Fi, enmascara las MAC (deja los 2 últimos bytes) y
// las IPv6 de enlace local, y cambia /Users/<nombre> y el nombre de esta Mac/usuario.
// Mismo criterio que depurar.pl (motor, muestras, buscar-secretos.sh) y que
// backend/app/services/nodo_egreso.py; casos compartidos en pruebas/depurador/.
import Foundation
import SystemConfiguration

enum Depurador {
    static let oculto = "[oculto]"

    struct Regla {
        let tipo: String
        let re: NSRegularExpression
        /// Recibe los grupos (0 = todo; "" si el grupo no participó) y devuelve el reemplazo.
        let reemplazo: ([String]) -> String
    }

    private static func re(_ p: String, _ o: NSRegularExpression.Options = []) -> NSRegularExpression {
        // Los patrones son constantes del programa: si alguno no compila es un error de desarrollo.
        try! NSRegularExpression(pattern: p, options: o)
    }

    private static let b64 = "[A-Za-z0-9+/_\\-]"

    /// El orden importa (igual que en depurar.pl): primero lo más específico.
    static let reglas: [Regla] = [
        Regla(tipo: "bloque PEM privado",
              re: re("-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----.*?(?:-----END \\1PRIVATE KEY-----|\\z)", [.dotMatchesLineSeparators]),
              reemplazo: { "-----BEGIN \($0[1])PRIVATE KEY----- \(oculto) -----END \($0[1])PRIVATE KEY-----" }),
        Regla(tipo: "cabecera con secreto",
              re: re("\\b(X-[A-Za-z0-9\\-]*(?:Key|Token|Secret|Signature|Auth[A-Za-z]*)|Authorization|Proxy-Authorization|Cookie|Set-Cookie)([ \\t]*:[ \\t]*)(?!\\[oculto\\])([^'\"\\s][^'\"\\r\\n]*)", [.caseInsensitive]),
              reemplazo: { "\($0[1])\($0[2])\(oculto)" }),
        Regla(tipo: "credencial en URL",
              re: re("(\\b[a-z][a-z0-9+.\\-]*://)[^/\\s:@'\"]*:[^/\\s@'\"]+@", [.caseInsensitive]),
              reemplazo: { "\($0[1])\(oculto)@" }),
        Regla(tipo: "Bearer/Basic",
              re: re("\\b(Bearer|Basic)[ \\t]+(?!\\[oculto\\])(?=[A-Za-z0-9\\-._~+/]*[0-9\\-._~+/])[A-Za-z0-9\\-._~+/]{12,}=*"),
              reemplazo: { "\($0[1]) \(oculto)" }),
        Regla(tipo: "llave de Google (AIza)", re: re("AIza[0-9A-Za-z_\\-]{30,}"), reemplazo: { _ in oculto }),
        Regla(tipo: "token de Google (AQ.)", re: re("\\bAQ\\.[0-9A-Za-z_\\-.]{16,}"), reemplazo: { _ in oculto }),
        Regla(tipo: "token OAuth de Google (ya29.)", re: re("\\bya29\\.[0-9A-Za-z_\\-.]{10,}"), reemplazo: { _ in oculto }),
        Regla(tipo: "token de GitHub", re: re("\\b(?:gh[pousr]_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})"), reemplazo: { _ in oculto }),
        Regla(tipo: "llave sk-", re: re("\\bsk-[0-9A-Za-z_\\-]{16,}"), reemplazo: { _ in oculto }),
        Regla(tipo: "token de Slack", re: re("\\bxox[abposr]-[0-9A-Za-z\\-]{10,}"), reemplazo: { _ in oculto }),
        Regla(tipo: "llave de AWS", re: re("\\b(?:AKIA|ASIA)[0-9A-Z]{16}\\b"), reemplazo: { _ in oculto }),
        Regla(tipo: "pase del evento (evp_)", re: re("\\bevp_[A-Za-z0-9_\\-]{16,}"), reemplazo: { _ in oculto }),
        Regla(tipo: "JWT", re: re("\\beyJ[0-9A-Za-z_\\-]{8,}(?:\\.[0-9A-Za-z_\\-]+){0,2}"), reemplazo: { _ in oculto }),
        Regla(tipo: "contraseña del Wi-Fi (QR)", re: re("(WIFI:[^\\n]*?\\bP:)((?:\\\\;|[^;\\n])+)"),
              reemplazo: { "\($0[1])\(oculto)" }),
        Regla(tipo: "contraseña del Wi-Fi (uci)",
              re: re("(\\boption[ \\t]+(?:key|password|sae_password|wpa_passphrase)[ \\t]+|\\.(?:key|password|sae_password|wpa_passphrase)=)('[^'\\n]*'|\"[^\"\\n]*\"|[^\\s'\"]+)"),
              reemplazo: { g in
                  let q = g[2].first.map { $0 == "'" || $0 == "\"" ? String($0) : "" } ?? ""
                  return "\(g[1])\(q)\(oculto)\(q)"
              }),
        Regla(tipo: "contraseña del Wi-Fi (MikroTik/wpa)",
              re: re("\\b(wpa2?-pre-shared-key|passphrase|psk|wpa_passphrase|sae_password)=(\"[^\"\\n]*\"|'[^'\\n]*'|[^\\s\"';]+)"),
              reemplazo: { "\($0[1])=\(oculto)" }),
        Regla(tipo: "variable de entorno con secreto",
              re: re("\\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PWD|CREDENTIALS?|DSN|PRIVATE|AUTH)[A-Z0-9_]*)=(?!\\[oculto\\])(\"[^\"\\n]*\"|'[^'\\n]*'|[^\\s\"']+)"),
              reemplazo: { "\($0[1])=\(oculto)" }),
        Regla(tipo: "bandera con secreto",
              re: re("((?<![\\w-])--?(?:[A-Za-z0-9]+[-_])*(?:api[-_]?key|key|token|secret|password|passwd|pass|pwd|auth|credentials?)(?:=|[ \\t]+))(?!\\[oculto\\])(\"[^\"\\n]*\"|'[^'\\n]*'|[^\\s\"']+)", [.caseInsensitive]),
              reemplazo: { "\($0[1])\(oculto)" }),
        Regla(tipo: "base64 junto a key/token/secret",
              re: re("(\\b(?:[A-Za-z0-9]+[-_])*(?:key|token|secret|passw(?:or)?d|credentials?|signature|auth(?:orization)?)s?\\b[\"']?[ \\t]*[:=]?[ \\t]*[\"']?)(\(b64){32,}={0,2})", [.caseInsensitive]),
              reemplazo: { "\($0[1])\(oculto)" }),
    ]

    private static let enlaceLocal = re("\\bfe80:[0-9A-Fa-f:]*", [.caseInsensitive])
    private static let mac = re("(?<![0-9A-Fa-f:\\-])([0-9A-Fa-f]{1,2})([:\\-])([0-9A-Fa-f]{1,2})\\2([0-9A-Fa-f]{1,2})\\2([0-9A-Fa-f]{1,2})\\2([0-9A-Fa-f]{1,2})\\2([0-9A-Fa-f]{1,2})(?![0-9A-Fa-f:\\-])")
    private static let rutaUsers = re("/Users/(?!USUARIO\\b|Shared\\b)[^/\\s\"':]+")
    private static let rutaHome = re("/home/(?!USUARIO\\b)[^/\\s\"':]+")
    private static let argumentos = re("\\s+(?=-|/|\"|'|[A-Za-z_][A-Za-z0-9_]*=|[a-z][a-z0-9+.\\-]*://).*$", [.dotMatchesLineSeparators])
    private static let columnasNumericas = re("^(\\s*(?:\\d+\\s+)*)(.*)$", [.dotMatchesLineSeparators])

    /// Nombre del equipo, nombre local, nombre de host, usuario y nombre completo de esta Mac
    /// (sin red: SystemConfiguration y gethostname, nunca `Host.current()` que puede consultar DNS).
    static let identidadLocal: [String] = {
        var c: [String] = []
        if let n = SCDynamicStoreCopyComputerName(nil, nil) as String? { c.append(n) }
        if let n = SCDynamicStoreCopyLocalHostName(nil) as String? { c.append(n) }
        var buf = [CChar](repeating: 0, count: 256)
        if gethostname(&buf, buf.count) == 0 {
            let h = String(cString: buf)
            c.append(h); c.append(String(h.split(separator: ".").first ?? ""))
        }
        c.append(NSUserName()); c.append(NSFullUserName())
        return normalizarIdentidad(c)
    }()

    static func normalizarIdentidad(_ c: [String]) -> [String] {
        var vistos = Set<String>()
        let comunes: Set<String> = ["root", "admin", "test", "localhost", "usuario"]
        return c.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { $0.count >= 3 && !comunes.contains($0.lowercased()) && vistos.insert($0.lowercased()).inserted }
            .sorted { $0.count > $1.count }
    }

    private static func reemplazar(_ s: String, _ re: NSRegularExpression, _ f: ([String]) -> String) -> String {
        let ns = s as NSString
        let ms = re.matches(in: s, range: NSRange(location: 0, length: ns.length))
        if ms.isEmpty { return s }
        let out = NSMutableString(string: s)
        for m in ms.reversed() {
            let g = (0..<m.numberOfRanges).map { i -> String in
                let r = m.range(at: i)
                return r.location == NSNotFound ? "" : ns.substring(with: r)
            }
            out.replaceCharacters(in: m.range, with: f(g))
        }
        return out as String
    }

    /// Texto depurado. `equipo`: nombres a cambiar por MAC-NODO (por defecto, los de esta Mac).
    static func texto(_ s: String, equipo: [String] = identidadLocal) -> String {
        guard !s.isEmpty else { return s }
        var t = s
        for r in reglas { t = reemplazar(t, r.re, r.reemplazo) }
        t = reemplazar(t, enlaceLocal) { _ in "fe80::\(oculto)" }
        t = reemplazar(t, mac) { g in "xx\(g[2])xx\(g[2])xx\(g[2])xx\(g[2])\(g[6])\(g[2])\(g[7])" }
        t = reemplazar(t, rutaUsers) { _ in "/Users/USUARIO" }
        t = reemplazar(t, rutaHome) { _ in "/home/USUARIO" }
        for e in equipo {
            let p = "(?<![A-Za-z0-9])" + NSRegularExpression.escapedPattern(for: e) + "(?![A-Za-z0-9])"
            t = reemplazar(t, re(p, [.caseInsensitive])) { _ in "MAC-NODO" }
        }
        return t
    }

    /// Una línea de `ps`: sólo las columnas numéricas iniciales (PID…) y el ejecutable, sin argumentos.
    static func soloEjecutable(_ linea: String) -> String {
        let ns = linea as NSString
        guard let m = columnasNumericas.firstMatch(in: linea, range: NSRange(location: 0, length: ns.length)) else { return linea }
        let pre = ns.substring(with: m.range(at: 1))
        let resto = reemplazar(ns.substring(with: m.range(at: 2)), argumentos) { _ in "" }
        return pre + resto
    }

    /// Lista de procesos: sin argumentos y depurada.
    static func procesos(_ s: String, equipo: [String] = identidadLocal) -> String {
        let lineas = s.components(separatedBy: "\n").map { soloEjecutable($0).replacingOccurrences(of: "\\s+$", with: "", options: .regularExpression) }
        return texto(lineas.joined(separator: "\n"), equipo: equipo)
    }

    /// Tipos de secreto que aparecen (para pruebas y avisos; nunca el valor). Lo ya depurado
    /// («[oculto]») no cuenta: la regla no cambiaría nada.
    static func tipos(_ s: String) -> [String] {
        reglas.filter { reemplazar(s, $0.re, $0.reemplazo) != s }.map(\.tipo)
    }
}
