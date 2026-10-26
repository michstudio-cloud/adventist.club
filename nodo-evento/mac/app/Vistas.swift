// Ventana única: botón grande, luces de estado y acciones. Español, sin terminal.
import AppKit
import CoreImage
import SwiftUI

// MARK: - Piezas comunes

enum Luz {
    case verde, ambar, rojo, gris
    var color: Color {
        switch self {
        case .verde: return Color(red: 0.13, green: 0.68, blue: 0.33)
        case .ambar: return Color(red: 0.95, green: 0.62, blue: 0.07)
        case .rojo: return Color(red: 0.86, green: 0.2, blue: 0.18)
        case .gris: return Color.gray.opacity(0.55)
        }
    }
}

struct Foco: View {
    let luz: Luz
    var tam: CGFloat = 18
    var body: some View {
        Circle().fill(luz.color)
            .frame(width: tam, height: tam)
            .overlay(Circle().stroke(Color.black.opacity(0.12), lineWidth: 1))
            .shadow(color: luz == .gris ? .clear : luz.color.opacity(0.55), radius: 5)
    }
}

struct Tarjeta: View {
    let luz: Luz
    let titulo: String
    let detalle: String
    var accion: (String, () -> Void)? = nil
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Foco(luz: luz, tam: 22).padding(.top, 3)
            VStack(alignment: .leading, spacing: 4) {
                Text(titulo).font(.system(size: 17, weight: .semibold))
                Text(detalle).font(.system(size: 14)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let accion {
                    Button(accion.0, action: accion.1).buttonStyle(.link).font(.system(size: 14, weight: .medium))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 92, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(luz == .gris ? Color.gray.opacity(0.2) : luz.color.opacity(0.45), lineWidth: 1.5))
    }
}

struct BotonGrande: View {
    let titulo: String
    let icono: String
    let color: Color
    var deshabilitado = false
    let accion: () -> Void
    var body: some View {
        Button(action: accion) {
            HStack(spacing: 12) {
                Image(systemName: icono).font(.system(size: 26, weight: .bold))
                Text(titulo).font(.system(size: 24, weight: .bold))
            }
            .frame(maxWidth: .infinity, minHeight: 74)
            .foregroundStyle(.white)
            .background(RoundedRectangle(cornerRadius: 16).fill(deshabilitado ? Color.gray.opacity(0.5) : color))
        }
        .buttonStyle(.plain)
        .disabled(deshabilitado)
    }
}

struct Mosaico: View {
    let titulo: String
    let sub: String
    let icono: String
    let accion: () -> Void
    var body: some View {
        Button(action: accion) {
            VStack(spacing: 8) {
                Image(systemName: icono).font(.system(size: 28)).foregroundStyle(Color.accentColor)
                Text(titulo).font(.system(size: 15, weight: .semibold)).multilineTextAlignment(.center)
                Text(sub).font(.system(size: 12)).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, minHeight: 112)
            .padding(.horizontal, 6)
            .background(RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.gray.opacity(0.25)))
        }
        .buttonStyle(.plain)
    }
}

/// Un mosaico que abre un menú (p. ej. «Ver sitio local» › Ver como juez / director).
struct MosaicoMenu<Contenido: View>: View {
    let titulo: String
    let sub: String
    let icono: String
    @ViewBuilder let opciones: () -> Contenido
    var body: some View {
        Menu {
            opciones()
        } label: {
            VStack(spacing: 8) {
                Image(systemName: icono).font(.system(size: 28)).foregroundStyle(Color.accentColor)
                Text(titulo).font(.system(size: 15, weight: .semibold)).multilineTextAlignment(.center)
                Text(sub).font(.system(size: 12)).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, minHeight: 112)
            .padding(.horizontal, 6)
            .background(RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.gray.opacity(0.25)))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
    }
}

struct VistaLog: View {
    @EnvironmentObject var motor: Motor
    var alto: CGFloat = 220
    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 3) {
                    ForEach(motor.log) { l in
                        HStack(alignment: .top, spacing: 6) {
                            switch l.tipo {
                            case .paso: Image(systemName: "arrow.right.circle.fill").foregroundStyle(Color.accentColor)
                            case .ok: Image(systemName: "checkmark.circle.fill").foregroundStyle(Luz.verde.color)
                            case .aviso: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Luz.ambar.color)
                            case .error: Image(systemName: "xmark.octagon.fill").foregroundStyle(Luz.rojo.color)
                            case .texto: Text(" ").frame(width: 14)
                            }
                            Text(l.texto)
                                .font(l.tipo == .texto ? .system(size: 12, design: .monospaced) : .system(size: 14, weight: l.tipo == .paso ? .semibold : .regular))
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .id(l.id)
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: alto)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .textBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.gray.opacity(0.25)))
            .onChange(of: motor.log.count) { _ in
                if let last = motor.log.last { proxy.scrollTo(last.id, anchor: .bottom) }
            }
        }
    }
}

struct BarraProgreso: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        if motor.ocupado {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(motor.tarea).font(.system(size: 15, weight: .semibold))
                    if !motor.paso.isEmpty { Text("· \(motor.paso)").font(.system(size: 14)).foregroundStyle(.secondary) }
                }
                ProgressView(value: max(0.03, motor.progreso))
            }
        }
    }
}

/// Encabezado de cada hoja con botón «Listo».
struct HojaMarco<Contenido: View>: View {
    let titulo: String
    let icono: String
    @Binding var abierta: Hoja?
    @ViewBuilder let contenido: () -> Contenido
    @EnvironmentObject var motor: Motor
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Image(systemName: icono).font(.system(size: 26)).foregroundStyle(Color.accentColor)
                Text(titulo).font(.system(size: 26, weight: .bold))
                Spacer()
                Button("Listo") { abierta = nil }.keyboardShortcut(.cancelAction).controlSize(.large)
                    .disabled(motor.ocupado)
            }
            contenido()
        }
        .padding(26)
        .frame(width: 860)
        .frame(minHeight: 560)
        .onAppear { if !motor.ocupado { motor.log = [] } }
    }
}

enum Hoja: String, Identifiable {
    case qr, preparar, subir, red, certificado, ajustes, secreto
    case interferencias, seguridad, dispositivos, vecinos
    var id: String { rawValue }
}

func haceCuanto(_ t: Double, ahora: Double) -> String {
    guard t > 0 else { return "nunca" }
    let s = Int(ahora - t)
    if s < 90 { return "hace \(max(s, 0)) s" }
    if s < 5400 { return "hace \(s / 60) min" }
    if s < 172800 { return "hace \(s / 3600) h" }
    return "hace \(s / 86400) días"
}

// MARK: - Ventana principal

struct Principal: View {
    @EnvironmentObject var motor: Motor
    @State private var hoja: Hoja?
    @State private var secretoPedido: Secreto = .eventPass
    @State private var confirmarApagar = false
    @ObservedObject private var rev = Revisiones.compartido
    @ObservedObject private var act = Actualizador.compartido
    @State private var avisoEncender: [Hallazgo] = []
    @State private var mostrarAvisoEncender = false
    @State private var mallaEstado = EstadoMalla.leer()

    var e: Estado? { motor.estado }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            encabezado
            botones
            esteNodo
            BarraProgreso()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    luces
                    acciones
                    if !motor.log.isEmpty && hoja == nil {
                        Text("Última operación: \(motor.tarea)").font(.system(size: 13, weight: .semibold)).foregroundStyle(.secondary)
                        VistaLog(alto: 170)
                    }
                }
            }
        }
        .padding(24)
        .sheet(item: $hoja) { h in
            Group {
                switch h {
                case .qr: HojaQR(abierta: $hoja)
                case .preparar: HojaPreparar(abierta: $hoja, pedirSecreto: pedir)
                case .subir: HojaSubir(abierta: $hoja, pedirSecreto: pedir)
                case .red: HojaRed(abierta: $hoja, pedirSecreto: pedir)
                case .certificado: HojaCertificado(abierta: $hoja, pedirSecreto: pedir)
                case .ajustes: HojaAjustes(abierta: $hoja, pedirSecreto: pedir)
                case .secreto: HojaSecreto(secreto: secretoPedido, abierta: $hoja)
                case .interferencias: HojaInterferencias(abierta: $hoja)
                case .seguridad: HojaSeguridad(abierta: $hoja)
                case .dispositivos: HojaDispositivos(abierta: $hoja)
                case .vecinos: HojaVecinos(abierta: $hoja)
                }
            }
            .environmentObject(motor)
        }
        .onReceive(NotificationCenter.default.publisher(for: Prueba.interno)) { n in
            guard let a = n.object as? String else { return }
            if a == "hoja:conectar" {
                if motor.modoManual { motor.guardarConfig("MODO", "auto") }
                hoja = .preparar
            } else if a == "hoja:router" { hoja = .red }
            else if a.hasPrefix("hoja:"), let h = Hoja(rawValue: String(a.dropFirst(5))) { hoja = h }
            else if a.hasPrefix("modo:") { motor.guardarConfig("MODO", a == "modo:manual" ? "manual" : "auto") }
            else if a.hasPrefix("red:") && hoja != .red { Prueba.redPendiente = String(a.dropFirst(4)); hoja = .red }
            else if a == "cerrar" { hoja = nil }
            else if a == "instalar" { motor.correr(["instalar"], titulo: "Instalando el nodo") }
            else if a == "encender" { motor.correr(["encender"], titulo: "Encendiendo el nodo") }
            else if a == "revisar" { rev.revisarTodo() }
            else if a == "actualizar:buscar" { act.buscar() }
            else if a == "actualizar:fondo" { act.buscarEnSegundoPlano() }
            else if a == "salir" { NSApp.terminate(nil) }
            else if a == "apagar" { motor.correr(["apagar"], titulo: "Apagando el nodo") }
            else if a.hasPrefix("motor:") { motor.correr(a.dropFirst(6).split(separator: " ").map(String.init), titulo: "Prueba: \(a.dropFirst(6))") }
        }
        .alert("Hay posibles interferencias", isPresented: $mostrarAvisoEncender) {
            Button("Encender de todos modos") { encender() }
            Button("Ver interferencias") { hoja = .interferencias }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text(avisoEncender.prefix(4).map { "• \($0.titulo)" }.joined(separator: "\n") + "\n\nEl nodo puede encender igual, pero los teléfonos podrían no llegar a él.")
        }
        .onAppear { rev.arrancar() }
        .onReceive(Timer.publish(every: 5, on: .main, in: .common).autoconnect()) { _ in mallaEstado = EstadoMalla.leer() }
        .alert("¿Apagar el nodo?", isPresented: $confirmarApagar) {
            Button("Apagar", role: .destructive) { motor.correr(["apagar"], titulo: "Apagando el nodo") }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Primero se guarda un respaldo. Los teléfonos guardan sus capturas en su cola y las envían cuando el nodo vuelva.")
        }
    }

    func encender() {
        // Los secretos del nodo se generan solos (nadie teclea nada en modo automático).
        motor.correr(["encender"], titulo: "Encendiendo el nodo")
    }

    func pedir(_ s: Secreto) {
        secretoPedido = s
        hoja = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { hoja = .secreto }
    }

    var encendido: Bool { e?.nodoArriba ?? false }

    var encabezado: some View {
        HStack(spacing: 16) {
            Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 64, height: 64)
            VStack(alignment: .leading, spacing: 2) {
                Text("Nodo Camporee").font(.system(size: 30, weight: .heavy))
                Text("Esta Mac sirve events.adventist.club a los teléfonos del evento, sin internet")
                    .font(.system(size: 14)).foregroundStyle(.secondary)
                EtiquetaPruebas()
            }
            Spacer()
            HStack(spacing: 10) {
                Foco(luz: estadoGeneral.0, tam: 26)
                Text(estadoGeneral.1).font(.system(size: 22, weight: .heavy))
            }
            .padding(.horizontal, 18).padding(.vertical, 10)
            .background(Capsule().fill(estadoGeneral.0.color.opacity(0.13)))
        }
    }

    var estadoGeneral: (Luz, String) {
        guard let e else { return (.gris, "…") }
        if motor.ocupado { return (.ambar, motor.tarea.uppercased().contains("APAG") ? "APAGANDO" : "TRABAJANDO") }
        if e.nodoSano { return (.verde, "ENCENDIDO") }
        if e.nodoArriba { return (.ambar, "ARRANCANDO") }
        return (.gris, "APAGADO")
    }

    var porInstalar: Bool { e?.instalacion?.pendiente ?? false }

    var botones: some View {
        HStack(spacing: 16) {
            if porInstalar && !encendido {
                BotonGrande(titulo: "Instalar", icono: "shippingbox.fill", color: Color(red: 0.16, green: 0.33, blue: 0.62),
                            deshabilitado: motor.ocupado || Ubicacion.provisional) {
                    motor.correr(["instalar"], titulo: "Instalando el nodo")
                }
            } else if encendido {
                BotonGrande(titulo: "Apagar", icono: "stop.circle.fill", color: Luz.rojo.color, deshabilitado: motor.ocupado) {
                    confirmarApagar = true
                }
            } else {
                BotonGrande(titulo: "Encender", icono: "power.circle.fill", color: Luz.verde.color, deshabilitado: motor.ocupado) {
                    // Antes, una revisión rápida de interferencias: avisa (no bloquea).
                    rev.revisarInterferencias { hs in
                        let imp = Revisiones.importantes(hs)
                        if imp.isEmpty { encender() } else { avisoEncender = imp; mostrarAvisoEncender = true }
                    }
                }
            }
            BotonGrande(titulo: "QR para teléfonos", icono: "qrcode", color: Color(red: 0.16, green: 0.33, blue: 0.62)) { hoja = .qr }
                .frame(maxWidth: 360)
        }
    }

    /// «Este nodo: «label» · captura: A, B · llegadas: sí · estado: preparado».
    var esteNodo: some View {
        let n = e?.nodo
        return HStack(spacing: 12) {
            Image(systemName: "desktopcomputer").font(.system(size: 22)).foregroundStyle(Color.accentColor)
            if let n, n.preparado {
                (Text("Este nodo: ").bold() + Text("«\(n.label ?? "—")»").bold()
                 + Text(" · captura: \(n.capturaTexto) · llegadas: \(n.check_in ? "sí" : "no") · estado: ")
                 + Text(n.estadoTexto).bold())
                    .font(.system(size: 15)).lineLimit(2)
            } else {
                Text("Esta Mac todavía no tiene evento. Con internet: «Preparar evento».").font(.system(size: 15))
            }
            Spacer()
            Text(motor.modoManual ? "Modo manual" : "Modo automático").font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.opacity(0.08)))
    }

    var luces: some View {
        let cols = Array(repeating: GridItem(.flexible(), spacing: 14, alignment: .top), count: 3)
        return LazyVGrid(columns: cols, spacing: 14) {
            tarjetaRouter
            tarjetaNodo
            tarjetaRed
            tarjetaInterferencias
            tarjetaSeguridad
            tarjetaVecinos
            tarjetaCert
            tarjetaRespaldo
            tarjetaTelefonos
            tarjetaEnergia
            tarjetaSueno
            tarjetaDisco
        }
    }

    var tarjetaRouter: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Red del evento", detalle: "…") }
        let modo = motor.cfg("RED_MODO")
        if !modo.isEmpty && modo != "glinet" {
            // Google Wifi, otro router o red del lugar: el estado es el del último «Revisar».
            let rev = motor.cfg("RED_REVISION")
            let necesitaDNS = modo == "google" || (modo == "otro" && motor.cfg("ROUTER_TIPO") == "dns")
            let titulo = "Red: \(OpcionRed.titulo(modo: modo))"
            if necesitaDNS && !(e.dns?.responde ?? false) {
                return Tarjeta(luz: .rojo, titulo: titulo, detalle: "El DNS de esta Mac está apagado: los teléfonos no encontrarán el sitio.", accion: ("Red del evento", { hoja = .red }))
            }
            if rev == "ok" { return Tarjeta(luz: .verde, titulo: "\(titulo) · Listo", detalle: motor.cfg("RED_REVISION_TXT")) }
            return Tarjeta(luz: rev.isEmpty ? .ambar : .rojo, titulo: titulo,
                           detalle: rev.isEmpty ? "Falta «Revisar». Esta Mac: \(e.red.ip.isEmpty ? "sin red" : e.red.ip)" : motor.cfg("RED_REVISION_TXT"),
                           accion: ("Red del evento", { hoja = .red }))
        }
        if e.red.ip_cambio {
            return Tarjeta(luz: .rojo, titulo: "Router GL.iNet",
                           detalle: "La IP de esta Mac cambió (\(e.red.ip_fija) → \(e.red.ip)). Los teléfonos no la encontrarán.",
                           accion: ("Volver a configurar el router", { hoja = .red }))
        }
        if e.red.en_red_router && e.red.router_responde {
            let tipo = e.red.tipo == "ethernet" ? "por cable" : "por Wi-Fi"
            let fija = e.red.ip_fija.isEmpty ? " · falta «Configurar router»" : ""
            return Tarjeta(luz: e.red.ip_fija.isEmpty ? .ambar : .verde, titulo: e.red.glinet ? "Router GL.iNet conectado" : "Router conectado",
                           detalle: "Esta Mac es \(e.red.ip) \(tipo)\(fija)",
                           accion: e.red.ip_fija.isEmpty ? ("Red del evento", { hoja = .red }) : nil)
        }
        if modo.isEmpty {
            return Tarjeta(luz: .ambar, titulo: "Elige la red del evento",
                           detalle: "GL.iNet, Google Wifi, otro router o la red del lugar. (Ahora: \(e.red.ip.isEmpty ? "sin red" : e.red.ip))",
                           accion: ("Red del evento", { hoja = .red }))
        }
        return Tarjeta(luz: .ambar, titulo: "Conecta el GL.iNet",
                       detalle: "No veo el router en \(motor.cfg("ROUTER_IP")). El nodo funciona igual; en cuanto conectes el cable o el Wi-Fi del GL.iNet, todo sigue solo. (Ahora: \(e.red.ip.isEmpty ? "sin red" : e.red.ip))")
    }

    var tarjetaNodo: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Nodo", detalle: "…") }
        let s = e.servicios
        let det = "Base \(trad(s.db)) · API \(trad(s.api)) · Sitio \(trad(s.web)) · HTTPS \(trad(s.caddy))"
        if let i = e.instalacion, i.pendiente {
            return Tarjeta(luz: .rojo, titulo: "Falta instalar",
                           detalle: Ubicacion.provisional
                               ? "Primero arrastra «Nodo Camporee» a la carpeta Aplicaciones y ábrela desde ahí."
                               : "Pulsa «Instalar» (\(i.motivo)). No necesita internet: todo viene dentro de la app.")
        }
        if !e.docker { return Tarjeta(luz: .gris, titulo: "Nodo apagado", detalle: "Docker (Colima) apagado. Pulsa «Encender».") }
        if !e.imagenes { return Tarjeta(luz: .rojo, titulo: "Falta construir", detalle: "Ajustes › Construir (con internet, una vez).", accion: ("Abrir ajustes", { hoja = .ajustes })) }
        if e.nodoSano { return Tarjeta(luz: .verde, titulo: "Sitio, API y base funcionando", detalle: det) }
        if s.db == "absent" { return Tarjeta(luz: .gris, titulo: "Nodo apagado", detalle: "Pulsa «Encender».") }
        return Tarjeta(luz: .ambar, titulo: "Nodo arrancando o con fallas", detalle: det)
    }

    var tarjetaRed: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Acceso desde los teléfonos", detalle: "…") }
        if !e.nodoArriba { return Tarjeta(luz: .gris, titulo: "Acceso desde los teléfonos", detalle: "Se revisa con el nodo encendido.") }
        if e.https_lan {
            var det = "https://events.adventist.club responde en \(e.red.ip) (puertos 80/443)."
            if e.firewall { det += " Firewall de macOS activo: si un teléfono no entra, Ajustes › Firewall." }
            return Tarjeta(luz: .verde, titulo: "Accesible en la red", detalle: det)
        }
        return Tarjeta(luz: .rojo, titulo: "No accesible en la red", detalle: "El sitio responde dentro de la Mac pero no en \(e.red.ip). Revisa el firewall (Ajustes).", accion: ("Ver ayuda", { hoja = .ajustes }))
    }

    var tarjetaCert: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Certificado", detalle: "…") }
        let c = e.certificado
        if !c.existe { return Tarjeta(luz: .rojo, titulo: "Sin certificado HTTPS", detalle: "Sácalo antes del viaje, con internet.", accion: ("Certificado", { hoja = .certificado })) }
        let dias = c.dias ?? 0
        let tipo: String = ["letsencrypt": "Let's Encrypt", "ca-local": "CA local (plan B)", "letsencrypt-ensayo": "Let's Encrypt de ENSAYO"][c.tipo] ?? c.tipo
        var luz: Luz = .verde
        var det = "\(tipo) · vence \(c.vence) (\(dias) días)"
        if dias < 0 { luz = .rojo; det = "\(tipo) · VENCIDO el \(c.vence)" }
        else if dias < 7 { luz = .rojo; det += " · renuévalo YA" }
        else if dias < 30 || !c.cubre_evento { luz = .ambar; det += c.cubre_evento ? " · renuévalo pronto" : " · vence antes de terminar el evento" }
        if c.tipo == "ca-local" { luz = luz == .verde ? .ambar : luz; det += " · cada iPhone debe instalar el perfil" }
        if c.tipo == "letsencrypt-ensayo" { luz = .rojo }
        return Tarjeta(luz: luz, titulo: "Certificado", detalle: det, accion: luz == .verde ? nil : ("Certificado", { hoja = .certificado }))
    }

    var tarjetaRespaldo: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Respaldo", detalle: "…") }
        let r = e.respaldo
        let ult = r.ultimo_t > 0 ? haceCuanto(r.ultimo_t, ahora: e.ahora) : "todavía no hay"
        let usb = r.usb ? "USB NODO-RESPALDO ✓ (\(r.usb_ultimo_t > 0 ? haceCuanto(r.usb_ultimo_t, ahora: e.ahora) : "sin copias aún"))" : "sin USB: sólo en Documentos"
        var luz: Luz = .verde
        let viejo = r.ultimo_t > 0 && (e.ahora - r.ultimo_t) > Double(r.cada_min * 60 * 2 + 120)
        if !e.nodoArriba { luz = .gris } else if r.ultimo_t == 0 || viejo { luz = .rojo } else if !r.usb { luz = .ambar }
        return Tarjeta(luz: luz, titulo: "Respaldo", detalle: "Último: \(ult) · cada \(r.cada_min) min · \(usb)",
                       accion: e.nodoArriba ? ("Respaldar ahora", { motor.correr(["respaldar"], titulo: "Respaldando") }) : nil)
    }

    var tarjetaTelefonos: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Dispositivos conectados", detalle: "…") }
        let n = e.telefonos.ips_10min
        let r = e.telefonos.peticiones_10min
        let real = e.puerta?.activa == true
        return Tarjeta(luz: !e.nodoArriba ? .gris : (n > 0 || r > 0 ? .verde : .gris),
                       titulo: real ? "\(n) dispositivo\(n == 1 ? "" : "s") conectado\(n == 1 ? "" : "s")" : (e.red.en_red_router ? "\(n) equipo\(n == 1 ? "" : "s") en la red del router" : "Dispositivos"),
                       detalle: "\(r) peticiones en 10 min\(real ? " · IP real de cada teléfono" : "")",
                       accion: e.nodoArriba ? ("Ver dispositivos", { hoja = .dispositivos }) : nil)
    }

    var tarjetaInterferencias: some View {
        let hs = rev.interferencias
        let imp = Revisiones.importantes(hs)
        let luz = Revisiones.luz(hs)
        let titulo = hs.isEmpty ? "Interferencias" : (imp.isEmpty ? "Sin interferencias" : "\(imp.count) posible\(imp.count == 1 ? "" : "s") interferencia\(imp.count == 1 ? "" : "s")")
        let det = hs.isEmpty ? (rev.revisando ? "Revisando VPN, filtros y firewall…" : "Aún no se revisa.")
            : (imp.isEmpty ? "VPN, filtros de red, firewall y DNS en orden (\(horaCorta(rev.cuandoInterferencias)))." : imp.prefix(2).map(\.titulo).joined(separator: " · "))
        return Tarjeta(luz: luz, titulo: titulo, detalle: det, accion: ("Revisar interferencias", { hoja = .interferencias }))
    }

    var tarjetaSeguridad: some View {
        let hs = rev.seguridad
        let imp = Revisiones.importantes(hs)
        let abierta = motor.cfg("RED_MODO") == "publica" && motor.cfg("PUBLICA_ABIERTA") != "0"
        var luz = Revisiones.luz(hs)
        if abierta && luz == .verde { luz = .ambar }
        let det = hs.isEmpty ? "Aún no se revisa." : (imp.isEmpty && !abierta ? "FileVault, firewall, compartir y bloqueo de pantalla en orden."
            : (imp.prefix(2).map(\.titulo) + (abierta ? ["Wi-Fi del evento abierto"] : [])).joined(separator: " · "))
        return Tarjeta(luz: luz, titulo: "Seguridad de la Mac", detalle: det, accion: ("Ver seguridad", { hoja = .seguridad }))
    }

    var tarjetaVecinos: some View {
        let m = mallaEstado
        let n = m.vecinos.count
        let conError = m.vecinos.filter { !EstadoMalla.texto($0["error"]).isEmpty || !(($0["traslapes"] as? [Any]) ?? []).isEmpty }.count
        let activa = e?.malla?.activa == true
        let luz: Luz = !activa ? .gris : (n == 0 ? .gris : (conError > 0 || !m.rechazados.isEmpty ? .ambar : .verde))
        let det: String
        if !activa { det = "Se activa al encender (comparte avances con otros nodos del evento en la red)." }
        else if m.sinPermisoRedLocal { return Tarjeta(luz: .rojo, titulo: "Nodos vecinos: falta un permiso", detalle: "Ajustes › Privacidad y seguridad › Red local › «Nodo Camporee».", accion: ("Nodos vecinos", { hoja = .vecinos })) }
        else if n == 0 { det = m.vistosBonjour.isEmpty ? "Buscando otros nodos del evento en esta red…" : "Encontrados \(m.vistosBonjour.count); conectando…" }
        else { det = m.vecinos.prefix(3).map { "«\(EstadoMalla.texto($0["label"]))» \(EstadoMalla.cuando($0["ultima_sync"]))" }.joined(separator: " · ") }
        return Tarjeta(luz: luz, titulo: n == 0 ? "Nodos vecinos" : "\(n) nodo\(n == 1 ? "" : "s") vecino\(n == 1 ? "" : "s")", detalle: det,
                       accion: ("Nodos vecinos", { hoja = .vecinos }))
    }

    var tarjetaEnergia: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Energía", detalle: "…") }
        if e.energia.bateria {
            let p = e.energia.porcentaje ?? 0
            return Tarjeta(luz: p < 25 ? .rojo : .ambar, titulo: "Con batería (\(p) %)", detalle: "Conecta el cargador de la Mac. Con batería baja la Mac puede apagarse.")
        }
        return Tarjeta(luz: .verde, titulo: "Con cargador", detalle: "Batería \(e.energia.porcentaje.map { "\($0) %" } ?? "—")")
    }

    var tarjetaSueno: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Sin reposo", detalle: "…") }
        if e.energia.despierto { return Tarjeta(luz: .verde, titulo: "La Mac no se dormirá", detalle: "La pantalla sí puede apagarse. NO cierres la tapa.") }
        return Tarjeta(luz: e.nodoArriba ? .ambar : .gris, titulo: "Reposo normal", detalle: e.nodoArriba ? "El nodo está arriba pero la Mac podría dormirse: pulsa Encender otra vez." : "Se activa al encender el nodo.")
    }

    var tarjetaDisco: some View {
        guard let e else { return Tarjeta(luz: .gris, titulo: "Disco", detalle: "…") }
        let g = e.disco_libre_gb
        return Tarjeta(luz: g < 5 ? .rojo : (g < 15 ? .ambar : .verde), titulo: "Disco: \(g) GB libres",
                       detalle: e.autoarranque ? "Se enciende sola al iniciar sesión en la Mac" : "Encendido manual (Ajustes › auto-arranque)")
    }

    var acciones: some View {
        HStack(spacing: 14) {
            Mosaico(titulo: "Preparar evento", sub: "víspera, con internet", icono: "arrow.down.circle") { hoja = .preparar }
            Mosaico(titulo: "Subir resultados", sub: "al volver internet", icono: "arrow.up.circle") { hoja = .subir }
            Mosaico(titulo: "Red del evento", sub: "GL.iNet · Google Wifi · otro", icono: "wifi.router") { hoja = .red }
            MosaicoMenu(titulo: "Ver sitio local", sub: "igual que en los teléfonos · ver como…", icono: "safari") {
                Button("Sitio local (inicio)") { motor.correr(["ver-sitio"], titulo: "Abriendo el sitio local") }
                Button("Panel de coordinación") { motor.correr(["ver-sitio", "coordinacion"], titulo: "Abriendo el panel") }
                Divider()
                Button("Ver como juez (sólo lectura)") { motor.correr(["ver-sitio", "juez"], titulo: "Abriendo la vista del juez") }
                Button("Ver como director… (elige el club)") { motor.correr(["ver-sitio", "director"], titulo: "Abriendo «Ver como…»") }
            }
            Mosaico(titulo: "Certificado", sub: "antes del viaje", icono: "lock.shield") { hoja = .certificado }
            Mosaico(titulo: "Ajustes", sub: "secretos · registros", icono: "gearshape") { hoja = .ajustes }
        }
    }

    func trad(_ s: String) -> String {
        ["healthy": "✓", "running": "✓", "starting": "arrancando", "unhealthy": "FALLA", "exited": "detenido", "absent": "—", "created": "creado", "restarting": "reiniciando"][s] ?? s
    }
}

// MARK: - QR

func imagenQR(_ texto: String, tam: CGFloat = 900) -> NSImage? {
    guard let f = CIFilter(name: "CIQRCodeGenerator") else { return nil }
    f.setValue(Data(texto.utf8), forKey: "inputMessage")
    f.setValue("M", forKey: "inputCorrectionLevel")
    guard let ci = f.outputImage else { return nil }
    let esc = tam / ci.extent.width
    let img = ci.transformed(by: CGAffineTransform(scaleX: esc, y: esc))
    let rep = NSCIImageRep(ciImage: img)
    let ns = NSImage(size: rep.size); ns.addRepresentation(rep)
    return ns
}

func escaparWifi(_ s: String) -> String {
    var o = ""
    for ch in s { if "\\;,:\"".contains(ch) { o.append("\\") }; o.append(ch) }
    return o
}

struct BloqueQR: View {
    let titulo: String
    let contenido: String
    let texto: String
    var body: some View {
        VStack(spacing: 10) {
            Text(titulo).font(.system(size: 22, weight: .bold))
            if let img = imagenQR(contenido) {
                Image(nsImage: img).interpolation(.none).resizable().frame(width: 300, height: 300)
                    .padding(14).background(Color.white).clipShape(RoundedRectangle(cornerRadius: 12))
            }
            Text(texto).font(.system(size: 18, weight: .semibold)).multilineTextAlignment(.center).textSelection(.enabled)
        }
        .frame(maxWidth: .infinity)
    }
}

struct HojaQR: View {
    @Binding var abierta: Hoja?
    @EnvironmentObject var motor: Motor
    var body: some View {
        HojaMarco(titulo: "QR para teléfonos", icono: "qrcode", abierta: $abierta) {
            let ssid = motor.cfg("WIFI_SSID")
            let clave = Llavero.leer(.wifi)
            HStack(alignment: .top, spacing: 24) {
                if !ssid.isEmpty, let clave {
                    BloqueQR(titulo: "1. Conéctate al Wi-Fi", contenido: "WIFI:T:WPA;S:\(escaparWifi(ssid));P:\(escaparWifi(clave));;",
                             texto: "Red: \(ssid)")
                }
                BloqueQR(titulo: ssid.isEmpty || clave == nil ? "Abre el sitio del evento" : "2. Abre el sitio",
                         contenido: "https://events.adventist.club", texto: "https://events.adventist.club")
                if motor.estado?.certificado.tipo == "ca-local", let ip = motor.estado?.red.ip, !ip.isEmpty {
                    BloqueQR(titulo: "Plan B: certificado", contenido: "http://\(ip)/nodo-ca.crt",
                             texto: "Instala el perfil y actívalo en Ajustes › General › Información › Confianza")
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Cómo entrar").font(.system(size: 17, weight: .bold))
                Text("• Conecta el teléfono al Wi-Fi «\(ssid.isEmpty ? "del GL.iNet" : ssid)». Si dice «sin internet», elige «Mantener conexión».")
                Text("• Abre https://events.adventist.club y entra con tu correo y contraseña de siempre (la primera vez en el nodo).")
                Text("• Si ya estabas dentro en internet, vuelve a iniciar sesión: el nodo tiene sus propias sesiones.")
                Text("• Sin señal, el teléfono guarda tus capturas («N por enviar») y las manda solo al acercarte al router.")
            }.font(.system(size: 15))
        }
    }
}

// MARK: - Preparar evento / Subir resultados

struct CamposEvento: View {
    @EnvironmentObject var motor: Motor
    @State private var evento = ""
    @State private var correo = ""
    @State private var etiqueta = ""
    var conEtiqueta = true
    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
            GridRow {
                Text("Evento (id)").font(.system(size: 14, weight: .semibold))
                TextField("10267b82-…", text: $evento).textFieldStyle(.roundedBorder).font(.system(size: 14, design: .monospaced))
                    .onSubmit { motor.guardarConfig("EVENTO_ID", evento) }
            }
            GridRow {
                Text("Correo de coordinación").font(.system(size: 14, weight: .semibold))
                TextField("quien firma en la bitácora", text: $correo).textFieldStyle(.roundedBorder)
                    .onSubmit { motor.guardarConfig("ACTOR_EMAIL", correo) }
            }
            if conEtiqueta {
                GridRow {
                    Text("Nombre del nodo").font(.system(size: 14, weight: .semibold))
                    TextField("Mac del nodo — Camporee", text: $etiqueta).textFieldStyle(.roundedBorder)
                        .onSubmit { motor.guardarConfig("NODO_LABEL", etiqueta) }
                }
            }
        }
        .onAppear { evento = motor.cfg("EVENTO_ID"); correo = motor.cfg("ACTOR_EMAIL"); etiqueta = motor.cfg("NODO_LABEL") }
        .onDisappear { guardar() }
    }
    func guardar() {
        if evento != motor.cfg("EVENTO_ID") { motor.guardarConfig("EVENTO_ID", evento.trimmingCharacters(in: .whitespaces)) }
        if correo != motor.cfg("ACTOR_EMAIL") { motor.guardarConfig("ACTOR_EMAIL", correo.trimmingCharacters(in: .whitespaces)) }
        if conEtiqueta && etiqueta != motor.cfg("NODO_LABEL") { motor.guardarConfig("NODO_LABEL", etiqueta) }
    }
}

struct FilaSecreto: View {
    @EnvironmentObject var motor: Motor
    let s: Secreto
    let pedir: (Secreto) -> Void
    var body: some View {
        let hay = motor.secretosPresentes[s] == true
        HStack(spacing: 10) {
            Foco(luz: hay ? .verde : (s.generado || s == .wifi || s == .prodEventPass ? .gris : .rojo), tam: 14)
            Text(s.titulo).font(.system(size: 14, weight: .semibold))
            Text(hay ? "guardado en el Llavero" : "falta").font(.system(size: 13)).foregroundStyle(.secondary)
            Spacer()
            if !s.generado { Button(hay ? "Reemplazar" : "Guardar…") { pedir(s) } }
        }
    }
}

struct HojaPreparar: View {
    @Binding var abierta: Hoja?
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    var body: some View {
        HojaMarco(titulo: "Preparar evento", icono: "arrow.down.circle", abierta: $abierta) {
            HStack { SelectorModo(); Spacer(); EtiquetaPruebas() }
            if motor.modoManual { PrepararManual(pedirSecreto: pedirSecreto) } else { FlujoAuto(proposito: .preparar) }
        }
    }
}

/// Modo manual (respaldo, sin la API): conexión de producción en el Llavero, como antes.
struct PrepararManual: View {
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var confirmar = false
    @State private var simulado = false
    @State private var actividades = ""
    @State private var llegadas = true
    var args: [String] {
        var a: [String] = []
        let ids = actividades.replacingOccurrences(of: " ", with: "")
        if ids.isEmpty { a.append("--all") } else { a += ["--activities", ids] }
        a.append(llegadas ? "--check-in" : "--no-check-in")
        return a
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Respaldo para cuando la API no está disponible. La víspera, CON internet y con el nodo encendido: copia el evento directo de la base de producción a esta Mac y deja la nube en «sólo consulta» para ese evento.")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            CamposEvento()
            HStack {
                Text("Actividades (ids, separados por coma)").font(.system(size: 14, weight: .semibold))
                TextField("vacío = todas", text: $actividades).textFieldStyle(.roundedBorder).font(.system(size: 13, design: .monospaced))
                Toggle("Mesa de llegadas", isOn: $llegadas).toggleStyle(.checkbox)
            }
            FilaSecreto(s: .prodURL, pedir: pedirSecreto)
            FilaSecreto(s: .prodEventPass, pedir: pedirSecreto)
            HStack(spacing: 12) {
                Button { motor.correr(["exportar", "--dry-run"] + args, titulo: "Simulando la copia") { _ in simulado = true } } label: {
                    Label("1. Simular (no cambia nada)", systemImage: "eye").frame(minWidth: 230, minHeight: 34)
                }.controlSize(.large)
                Button { confirmar = true } label: {
                    Label("2. Copiar al nodo y bloquear la nube", systemImage: "arrow.down.to.line").frame(minWidth: 290, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).disabled(!simulado)
                Spacer()
                Button("Ver estado") { motor.correr(["sync-estado"], titulo: "Consultando ambos lados") }
            }
            .disabled(motor.ocupado)
            BarraProgreso()
            VistaLog(alto: 190)
        }
        .alert("¿Copiar y bloquear?", isPresented: $confirmar) {
            Button("Copiar y bloquear") { motor.correr(["exportar"] + args, titulo: "Copiando el evento al nodo") }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Desde ahora la nube responde «en nodo local» a toda escritura de este evento. Se libera al «Subir resultados» final.")
        }
    }
}

struct HojaSubir: View {
    @Binding var abierta: Hoja?
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    var body: some View {
        HojaMarco(titulo: "Subir resultados", icono: "arrow.up.circle", abierta: $abierta) {
            HStack { SelectorModo(); Spacer(); EtiquetaPruebas() }
            if motor.modoManual { SubirManual(pedirSecreto: pedirSecreto) } else { FlujoAuto(proposito: .subir) }
        }
    }
}

struct SubirManual: View {
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var revisado = false
    @State private var confirmar = false
    @State private var avance = false
    var extra: [String] { avance ? ["--partial"] : [] }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Respaldo sin la API: sube directo a la base de producción las llegadas, evaluaciones, revisiones y ajustes capturados en el nodo. Primero se revisa (simulación); nada se sube sin confirmar. Repetirlo no duplica nada.")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            CamposEvento(conEtiqueta: false)
            Picker("", selection: $avance) {
                Text("Subir final (cierro este nodo)").tag(false)
                Text("Subir avance (sigo capturando)").tag(true)
            }.pickerStyle(.radioGroup).labelsHidden().onChange(of: avance) { _ in revisado = false }
            FilaSecreto(s: .prodURL, pedir: pedirSecreto)
            HStack(spacing: 12) {
                Button {
                    motor.correr(["importar", "--dry-run"] + extra, titulo: "Revisando qué se subiría") { c in revisado = (c == 0 || c == 2) }
                } label: { Label("1. Revisar (simulación)", systemImage: "eye").frame(minWidth: 220, minHeight: 34) }
                    .controlSize(.large)
                Button { confirmar = true } label: {
                    Label("2. Subir a producción", systemImage: "icloud.and.arrow.up").frame(minWidth: 220, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).disabled(!revisado)
                Spacer()
            }
            .disabled(motor.ocupado)
            if motor.conflictos && !motor.ocupado {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Hay conflictos: algo cambió en la nube Y en el nodo. Esas filas no se suben hasta decidir.", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(Luz.ambar.color).font(.system(size: 15, weight: .semibold))
                    HStack(spacing: 12) {
                        Button("Gana el nodo (lo capturado en el evento)") { motor.correr(["importar", "--prefer", "node"] + extra, titulo: "Subiendo (gana el nodo)") }
                        Button("Gana la nube") { motor.correr(["importar", "--prefer", "prod"] + extra, titulo: "Subiendo (gana la nube)") }
                        Button("Dejar pendiente") { motor.conflictos = false }
                    }.controlSize(.large)
                }
                .padding(12).background(RoundedRectangle(cornerRadius: 10).fill(Luz.ambar.color.opacity(0.12)))
            }
            BarraProgreso()
            VistaLog(alto: 200)
        }
        .alert("¿Subir a producción?", isPresented: $confirmar) {
            Button("Respaldar y subir") {
                motor.correr(["respaldar"], titulo: "Respaldo antes de subir") { _ in
                    motor.correr(["importar"] + extra, titulo: "Subiendo resultados a producción", limpiar: false)
                }
            }
            Button("Cancelar", role: .cancel) {}
        } message: { Text("Se escribe en la base de producción en una sola transacción. Si hay conflictos, esas filas no se aplican y el bloqueo sigue.") }
    }
}

// MARK: - Certificado

struct HojaCertificado: View {
    @Binding var abierta: Hoja?
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var correo = ""
    @State private var ensayo = false
    @State private var confirmarLocal = false
    var body: some View {
        HojaMarco(titulo: "Certificado HTTPS", icono: "lock.shield", abierta: $abierta) {
            if let c = motor.estado?.certificado, c.existe {
                Text("Actual: \(c.tipo) · vence \(c.vence) (\(c.dias ?? 0) días) · \(c.nombres)").font(.system(size: 14, weight: .semibold))
            } else {
                Text("Todavía no hay certificado.").font(.system(size: 14, weight: .semibold)).foregroundStyle(Luz.rojo.color)
            }
            GroupBox("Plan A (recomendado): Let's Encrypt — con internet, 1–3 semanas antes") {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Un certificado real para events/admin/api.adventist.club: los teléfonos abren el sitio sin avisos, con cámara y caché. Usa un registro temporal en Cloudflare; no cambia el DNS público. Dura ~90 días.")
                        .font(.system(size: 13)).fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Text("Correo para avisos").font(.system(size: 14, weight: .semibold))
                        TextField("correo@…", text: $correo).textFieldStyle(.roundedBorder)
                    }
                    FilaSecreto(s: .cloudflare, pedir: pedirSecreto)
                    HStack {
                        Toggle("Ensayo (certificado de prueba, no confiable)", isOn: $ensayo)
                        Spacer()
                        Button("Obtener / renovar") {
                            if correo != motor.cfg("LE_EMAIL") { motor.guardarConfig("LE_EMAIL", correo) }
                            motor.correr(["cert-letsencrypt"] + (ensayo ? ["--staging"] : []), titulo: "Pidiendo el certificado")
                        }.controlSize(.large).buttonStyle(.borderedProminent)
                            .disabled(motor.ocupado || correo.isEmpty || motor.secretosPresentes[.cloudflare] != true)
                    }
                }.padding(6)
            }
            GroupBox("Plan B: certificado local (sin internet, último recurso)") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Crea una autoridad propia en esta Mac. Cada teléfono debe INSTALARLA y CONFIARLA (iPhone: abrir http://<IP de la Mac>/nodo-ca.crt → Ajustes › General › VPN y gestión de dispositivos › Instalar → Ajustes › General › Información › Ajustes de confianza de certificados → activar). Si el teléfono ya visitó el sitio real, el navegador puede negarse. Mucha fricción con 100 teléfonos.")
                        .font(.system(size: 13)).fixedSize(horizontal: false, vertical: true)
                    HStack { Spacer(); Button("Usar certificado local…") { confirmarLocal = true }.disabled(motor.ocupado) }
                }.padding(6)
            }
            BarraProgreso()
            VistaLog(alto: 150)
        }
        .onAppear { correo = motor.cfg("LE_EMAIL") }
        .alert("¿Reemplazar por un certificado local?", isPresented: $confirmarLocal) {
            Button("Usar certificado local") { motor.correr(["cert-local"], titulo: "Creando certificado local") }
            Button("Cancelar", role: .cancel) {}
        } message: { Text("Reemplaza el certificado actual. Los teléfonos tendrán que instalar el perfil de la CA.") }
    }
}

// MARK: - Ajustes y secretos

struct HojaAjustes: View {
    @Binding var abierta: Hoja?
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var auto = false
    @State private var avanzado = false
    var body: some View {
        HojaMarco(titulo: "Ajustes", icono: "gearshape", abierta: $abierta) {
            GroupBox("Cómo se prepara y se sube el evento") {
                VStack(alignment: .leading, spacing: 6) {
                    HStack { SelectorModo(); Spacer(); EtiquetaPruebas() }
                    Text(motor.modoManual
                         ? "Manual: con la conexión de producción y el secreto de pases de producción (Avanzado). Sólo si la API no está disponible."
                         : "Automático: «Conectar con adventist.club» con tu cuenta. Nada que teclear: los secretos del nodo los genera la app.")
                        .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.padding(6)
            }
            DisclosureGroup(isExpanded: $avanzado) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Secretos opcionales (en el Llavero de macOS; nunca en archivos)").font(.system(size: 12)).foregroundStyle(.secondary)
                    ForEach(Secreto.allCases.filter { !$0.generado && (motor.modoManual || !$0.deModoManual) }) { s in FilaSecreto(s: s, pedir: pedirSecreto) }
                    Text("Generados por la app: " + Secreto.allCases.filter(\.generado).map { "\($0.titulo) \(motor.secretosPresentes[$0] == true ? "✓" : "(al encender)")" }.joined(separator: " · "))
                        .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.padding(.top, 6)
            } label: {
                Text("Avanzado").font(.system(size: 14, weight: .semibold))
            }
            GroupBox("Arranque") {
                VStack(alignment: .leading, spacing: 8) {
                    Toggle("Encender el nodo al abrir la Mac (al iniciar sesión)", isOn: $auto)
                        .onChange(of: auto) { v in
                            if v != (motor.estado?.autoarranque ?? false) { motor.correr(["autoarranque", v ? "on" : "off"], titulo: "Auto-arranque") }
                        }
                    Text("La máquina de Docker usa \(motor.cfg("COLIMA_CPU")) núcleos y \(motor.cfg("COLIMA_MEM")) GB (perfil «\(motor.cfg("COLIMA_PROFILE"))»).")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                }.padding(6)
            }
            GroupBox("Mantenimiento") {
                HStack(spacing: 10) {
                    // «Construir» sólo si esta Mac tiene los repos; la versión portátil se reinstala desde la app.
                    if motor.estado?.instalacion?.puede_construir ?? true {
                        Button("Construir / actualizar (con internet)") { motor.correr(["construir"], titulo: "Construyendo el sistema") }
                    }
                    if motor.estado?.instalacion?.paquete == true {
                        Button("Volver a instalar (sin internet)") { motor.correr(["instalar"], titulo: "Instalando el nodo") }
                            .disabled(Ubicacion.provisional)
                    }
                    Button("Registros") { motor.correr(["registros"], titulo: "Registros") }
                    Button("Carpeta de respaldos") { NSWorkspace.shared.open(URL(fileURLWithPath: motor.cfg("RESPALDO_LOCAL"))) }
                    Button("Revisar red") { motor.correr(["red-revisar"], titulo: "Revisando la red") }
                }.disabled(motor.ocupado).padding(6)
            }
            GroupBox("Actualizaciones") { SeccionActualizaciones().padding(6).frame(maxWidth: .infinity, alignment: .leading) }
            if let i = motor.estado?.instalacion, i.paquete {
                Text("Versión portátil \(i.version_paquete) · instalada: \(i.version_instalada.isEmpty ? "—" : i.version_instalada) · Colima y Docker: \(i.herramientas == "app" ? "los de la app" : (i.herramientas == "sistema" ? "los de esta Mac" : "faltan"))")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            if motor.estado?.firewall == true {
                Text("Firewall de macOS activo. Si al encender macOS pregunta «¿Permitir conexiones entrantes?» para «Nodo Camporee» (la puerta del nodo) o limactl, elige «Permitir». Si un teléfono no entra: «Revisar interferencias».")
                    .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            BarraProgreso()
            VistaLog(alto: 160)
        }
        .onAppear { auto = motor.estado?.autoarranque ?? false; motor.refrescarSecretos() }
    }
}

struct HojaSecreto: View {
    let secreto: Secreto
    @Binding var abierta: Hoja?
    @EnvironmentObject var motor: Motor
    @State private var valor = ""
    @State private var error = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Image(systemName: "key.fill").font(.system(size: 24)).foregroundStyle(Color.accentColor)
                Text(secreto.titulo).font(.system(size: 24, weight: .bold))
            }
            Text(secreto.ayuda).font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            SecureField("pega aquí el valor (no se muestra)", text: $valor).textFieldStyle(.roundedBorder).font(.system(size: 15))
                .onSubmit(guardar)
            if !error.isEmpty { Text(error).foregroundStyle(Luz.rojo.color) }
            Text("Se guarda en el Llavero de esta Mac. No se escribe en archivos ni en registros.").font(.system(size: 12)).foregroundStyle(.secondary)
            HStack {
                if Llavero.existe(secreto) {
                    Button("Borrar", role: .destructive) { Llavero.borrar(secreto); motor.refrescarSecretos(); abierta = nil }
                }
                Spacer()
                Button("Cancelar") { valor = ""; abierta = nil }.keyboardShortcut(.cancelAction)
                Button("Guardar", action: guardar).keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).disabled(valor.isEmpty)
            }.controlSize(.large)
        }
        .padding(26)
        .frame(width: 620)
        .onDisappear { valor = "" }
    }
    func guardar() {
        if secreto == .prodURL && !valor.hasPrefix("postgres") { error = "Debe empezar con postgresql://"; return }
        if Llavero.guardar(secreto, valor) { valor = ""; motor.refrescarSecretos(); abierta = nil } else { error = "No se pudo guardar en el Llavero." }
    }
}
