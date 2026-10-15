// Modo automático: «Conectar con adventist.club» → «Mis eventos» → alcance → Preparar, y
// «Subir resultados» (avance / final) con revisión previa. El token vive sólo en `SesionAPI`.
import SwiftUI

enum Proposito { case preparar, subir }

/// Selector «Automático / Manual» (se recuerda en config.env: MODO).
struct SelectorModo: View {
    @EnvironmentObject var motor: Motor
    @State private var manual = false
    var body: some View {
        Picker("", selection: $manual) {
            Text("Automático (recomendado)").tag(false)
            Text("Manual (respaldo, sin la API)").tag(true)
        }
        .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 460)
        .onAppear { manual = motor.modoManual }
        .onChange(of: motor.modoManual) { v in manual = v }
        .onChange(of: manual) { v in if v != motor.modoManual { motor.guardarConfig("MODO", v ? "manual" : "auto") } }
        .disabled(motor.ocupado)
    }
}

struct EtiquetaPruebas: View {
    var body: some View {
        if SesionAPI.esPrueba {
            Text("PRUEBAS: \(SesionAPI.base)")
                .font(.system(size: 12, weight: .heavy, design: .monospaced)).foregroundStyle(.white)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(Luz.rojo.color))
        }
    }
}

/// Encabezado de cada paso (número grande + título).
struct TituloPaso: View {
    let n: Int
    let texto: String
    var body: some View {
        HStack(spacing: 10) {
            Text("\(n)").font(.system(size: 15, weight: .heavy)).foregroundStyle(.white)
                .frame(width: 28, height: 28).background(Circle().fill(Color.accentColor))
            Text(texto).font(.system(size: 19, weight: .bold))
        }
    }
}

struct Aviso: View {
    let texto: String
    var luz: Luz = .ambar
    var icono = "exclamationmark.triangle.fill"
    var body: some View {
        Label(texto, systemImage: icono)
            .font(.system(size: 14, weight: .semibold)).foregroundStyle(luz.color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(luz.color.opacity(0.12)))
    }
}

struct FlujoAuto: View {
    let proposito: Proposito
    @EnvironmentObject var motor: Motor
    @StateObject private var sesion = SesionAPI()

    enum Paso { case conectar, codigo, eventos, alcance, trabajo, subir }
    @State private var paso: Paso = .conectar
    @State private var correo = ""
    @State private var clave = ""
    @State private var codigo = ""
    @State private var temp = ""
    @State private var error = ""
    @State private var cargando = false
    @State private var eventos: [EventoAPI] = []
    @State private var elegido: EventoAPI?
    // Alcance
    @State private var etiqueta = ""
    @State private var seleccion: Set<String> = []
    @State private var todas = false
    @State private var llegadas = true
    @State private var reemplazarCopia = false
    @State private var reemplazarNodo = true
    /// La nube registró el nodo pero la carga falló: el siguiente intento lo reemplaza.
    @State private var nodoFallido: String?
    // Subir
    @State private var final = false
    @State private var revisado = false
    @State private var preferir = ""       // "", "node", "prod"
    @State private var confirmarSubir = false
    @State private var terminado = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch paso {
            case .conectar: vistaConectar
            case .codigo: vistaCodigo
            case .eventos: vistaEventos
            case .alcance: vistaAlcance
            case .trabajo: vistaTrabajo
            case .subir: vistaSubir
            }
        }
        .onDisappear { sesion.olvidar(); clave = ""; codigo = "" }
        .onChange(of: sesion.conectado) { c in
            if !c && paso != .conectar && paso != .codigo && !motor.ocupado { volverALogin("La sesión de 15 minutos terminó: vuelve a iniciar sesión.") }
        }
        .onReceive(NotificationCenter.default.publisher(for: Prueba.interno)) { n in pruebas(n.object as? String ?? "") }
    }

    // MARK: Paso 1: iniciar sesión

    var vistaConectar: some View {
        VStack(alignment: .leading, spacing: 12) {
            TituloPaso(n: 1, texto: "Conectar con adventist.club")
            Text(proposito == .preparar
                 ? "Con internet (en casa, la víspera). Entra con tu cuenta de coordinación: verás tus eventos y eliges qué capturará esta Mac. No hay que copiar contraseñas de producción."
                 : "Con internet de verdad (no el Wi-Fi del evento). Entra con tu cuenta de coordinación para subir lo capturado en este nodo.")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                GridRow {
                    Text("Correo").font(.system(size: 14, weight: .semibold))
                    TextField("tu@correo", text: $correo).textFieldStyle(.roundedBorder).font(.system(size: 15))
                }
                GridRow {
                    Text("Contraseña").font(.system(size: 14, weight: .semibold))
                    SecureField("la de adventist.club (no se guarda)", text: $clave).textFieldStyle(.roundedBorder).font(.system(size: 15))
                        .onSubmit(entrar)
                }
            }.frame(maxWidth: 560)
            if !error.isEmpty { Aviso(texto: error, luz: .rojo, icono: "xmark.octagon.fill") }
            HStack {
                Button(action: entrar) {
                    Label(cargando ? "Conectando…" : "Entrar", systemImage: "person.badge.key").frame(minWidth: 180, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(correo.isEmpty || clave.isEmpty || cargando)
                if cargando { ProgressView().controlSize(.small) }
                Spacer()
            }
            Text("La sesión vive sólo en la memoria de la app: se borra al cerrar esta ventana o a los 15 minutos. Nunca se guarda en el disco.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
        }
    }

    func entrar() {
        guard !correo.isEmpty, !clave.isEmpty, !cargando else { return }
        error = ""; cargando = true
        Task {
            defer { cargando = false }
            do {
                switch try await sesion.entrar(correo: correo.trimmingCharacters(in: .whitespaces), clave: clave) {
                case .listo: clave = ""; await cargarEventos()
                case .pideCodigo(let t): clave = ""; temp = t; paso = .codigo
                }
            } catch { self.error = error.localizedDescription }
        }
    }

    var vistaCodigo: some View {
        VStack(alignment: .leading, spacing: 12) {
            TituloPaso(n: 1, texto: "Código de verificación")
            Text("Tu cuenta tiene verificación en dos pasos. Escribe el código de 6 dígitos de tu app de autenticación (o un código de recuperación).")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            TextField("123456", text: $codigo).textFieldStyle(.roundedBorder)
                .font(.system(size: 28, weight: .bold, design: .monospaced)).frame(maxWidth: 240)
                .onSubmit(verificar)
            if !error.isEmpty { Aviso(texto: error, luz: .rojo, icono: "xmark.octagon.fill") }
            HStack {
                Button(action: verificar) { Label("Verificar", systemImage: "checkmark.shield").frame(minWidth: 160, minHeight: 34) }
                    .controlSize(.large).buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(codigo.count < 6 || cargando)
                Button("Volver") { paso = .conectar; codigo = ""; error = "" }.controlSize(.large)
                if cargando { ProgressView().controlSize(.small) }
            }
        }
    }

    func verificar() {
        guard codigo.count >= 6, !cargando else { return }
        error = ""; cargando = true
        Task {
            defer { cargando = false }
            do { try await sesion.verificar(temp: temp, codigo: codigo); codigo = ""; temp = ""; await cargarEventos() }
            catch { self.error = error.localizedDescription }
        }
    }

    func cargarEventos() async {
        do {
            eventos = try await sesion.misEventos()
            error = ""
            if proposito == .subir {
                let id = motor.cfg("EVENTO_ID")
                elegido = eventos.first { $0.id == id } ?? (eventos.count == 1 ? eventos.first : nil)
                paso = elegido == nil ? .eventos : .subir
            } else {
                paso = .eventos
            }
        } catch ErrorAPI.reLogin { volverALogin("La sesión venció: vuelve a iniciar sesión.") }
        catch { self.error = error.localizedDescription; paso = .eventos }
    }

    func volverALogin(_ m: String) {
        sesion.olvidar(); paso = .conectar; error = m; revisado = false
    }

    // MARK: Paso 2: mis eventos

    var vistaEventos: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                TituloPaso(n: 2, texto: "Mis eventos")
                Spacer()
                Text("Conectado: \(sesion.correo)").font(.system(size: 13)).foregroundStyle(.secondary)
            }
            if !error.isEmpty { Aviso(texto: error, luz: .rojo, icono: "xmark.octagon.fill") }
            if eventos.isEmpty && error.isEmpty {
                Aviso(texto: "Tu cuenta no coordina ningún evento abierto. Pide al administrador del evento que te agregue como coordinador.", luz: .ambar)
            }
            ScrollView {
                VStack(spacing: 10) {
                    ForEach(eventos) { ev in
                        Button { elegir(ev) } label: { FilaEvento(ev: ev) }.buttonStyle(.plain)
                    }
                }
            }.frame(maxHeight: 330)
        }
    }

    func elegir(_ ev: EventoAPI) {
        elegido = ev
        if proposito == .subir { paso = .subir; return }
        // Valores iniciales: nombre de la Mac, nada elegido; «Todas» sólo si no hay otros nodos abiertos.
        let otros = ev.nodos.filter { $0.abierto && $0.id != motor.estado?.nodo?.id }
        etiqueta = motor.estado?.nodo?.label ?? (Host.current().localizedName ?? "Nodo 1")
        seleccion = []
        todas = otros.isEmpty
        llegadas = true
        reemplazarNodo = ev.nodos.contains { $0.abierto && $0.id == motor.estado?.nodo?.id }
        reemplazarCopia = false
        paso = .alcance
    }

    // MARK: Paso 3: qué captura este nodo

    var otrosNodos: [NodoAPI] {
        guard let ev = elegido else { return [] }
        return ev.nodos.filter { $0.abierto && !($0.id == motor.estado?.nodo?.id && reemplazarNodo) }
    }

    /// Actividad → nombre del otro nodo que ya la captura (un grupo incluye a sus hijas).
    var ocupadas: [String: String] {
        guard let ev = elegido else { return [:] }
        var m: [String: String] = [:]
        for n in otrosNodos {
            guard let s = n.scope else { continue }
            if s.all { for a in ev.actividades { m[a.id] = n.label }; continue }
            for id in s.activity_ids {
                m[id] = n.label
                for h in ev.actividades where h.parent_id == id { m[h.id] = n.label }
            }
        }
        return m
    }

    /// Actividades en orden de árbol: (actividad, nivel).
    var arbol: [(ActividadAPI, Int)] {
        guard let ev = elegido else { return [] }
        let todas = ev.actividades
        let ids = Set(todas.map(\.id))
        func hijos(_ p: String?) -> [ActividadAPI] {
            todas.filter { p == nil ? ($0.parent_id == nil || !ids.contains($0.parent_id!)) : $0.parent_id == p }
                .sorted { ($0.position ?? 0, $0.name) < ($1.position ?? 0, $1.name) }
        }
        var r: [(ActividadAPI, Int)] = []
        func bajar(_ a: ActividadAPI, _ n: Int) { r.append((a, n)); for h in hijos(a.id) { bajar(h, n + 1) } }
        for a in hijos(nil) { bajar(a, 0) }
        return r
    }

    func padreElegido(_ a: ActividadAPI) -> Bool { a.parent_id.map { seleccion.contains($0) } ?? false }

    var vistaAlcance: some View {
        let ocup = ocupadas
        let hayOtros = !otrosNodos.isEmpty
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                TituloPaso(n: 3, texto: "¿Qué captura esta Mac?")
                Spacer()
                Button("‹ Otro evento") { paso = .eventos }
            }
            if let ev = elegido {
                Text("\(ev.name)\(ev.fechas.isEmpty ? "" : " · \(ev.fechas)")").font(.system(size: 15, weight: .semibold))
            }
            HStack {
                Text("Nombre de este nodo").font(.system(size: 14, weight: .semibold))
                TextField("p. ej. Zona Misión Galáctica", text: $etiqueta).textFieldStyle(.roundedBorder).frame(maxWidth: 360)
            }
            if hayOtros {
                Aviso(texto: "Otros nodos abiertos: " + otrosNodos.map { "«\($0.label)» (\($0.coordinator_name ?? $0.coordinator_email ?? "—"))" }.joined(separator: ", ") + ". Sus actividades aparecen en gris.", luz: .ambar, icono: "person.2.fill")
            }
            if let viejo = elegido?.nodos.first(where: { $0.abierto && $0.id == motor.estado?.nodo?.id }) {
                Toggle("Reemplazar el nodo anterior de esta Mac («\(viejo.label)»)", isOn: $reemplazarNodo)
            }
            HStack(spacing: 18) {
                if !hayOtros {
                    Toggle("Todas las actividades", isOn: $todas).toggleStyle(.checkbox).font(.system(size: 15, weight: .semibold))
                }
                Toggle("Mesa de llegadas (check-in)", isOn: $llegadas).toggleStyle(.checkbox).font(.system(size: 15, weight: .semibold))
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(arbol, id: \.0.id) { item in
                        let a = item.0
                        let de = ocup[a.id]
                        let implicita = todas || padreElegido(a)
                        HStack(spacing: 6) {
                            Toggle(isOn: Binding(get: { implicita || seleccion.contains(a.id) },
                                                 set: { v in if v { seleccion.insert(a.id) } else { seleccion.remove(a.id) } })) {
                                Text(a.name).font(.system(size: 14, weight: item.1 == 0 ? .semibold : .regular))
                            }
                            .toggleStyle(.checkbox)
                            .disabled(de != nil || implicita)
                            if let de { Text("— la captura «\(de)»").font(.system(size: 12)).foregroundStyle(.secondary) }
                        }
                        .padding(.leading, CGFloat(item.1) * 22)
                    }
                    if arbol.isEmpty { Text("Este evento no tiene actividades todavía.").foregroundStyle(.secondary) }
                }
                .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            }
            .frame(maxHeight: 200)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .textBackgroundColor)))
            if motor.estado?.nodo?.preparado == true {
                Toggle("Borrar la copia que ya tiene esta Mac (sus capturas no subidas se pierden)", isOn: $reemplazarCopia).foregroundStyle(Luz.rojo.color)
            }
            if !error.isEmpty { Aviso(texto: error, luz: .rojo, icono: "xmark.octagon.fill") }
            HStack {
                Button(action: preparar) {
                    Label("Preparar este evento", systemImage: "arrow.down.circle.fill").frame(minWidth: 240, minHeight: 36)
                }.controlSize(.large).buttonStyle(.borderedProminent)
                    .disabled(motor.ocupado || etiqueta.trimmingCharacters(in: .whitespaces).isEmpty || (!todas && seleccion.isEmpty && !llegadas))
                Text(resumenAlcance).font(.system(size: 13)).foregroundStyle(.secondary)
                Spacer()
            }
        }
    }

    var resumenAlcance: String {
        let acts = todas ? "todas las actividades" : (seleccion.isEmpty ? "ninguna actividad" : "\(seleccion.count) actividad\(seleccion.count == 1 ? "" : "es") (con sus hijas)")
        return "Captura: \(acts) · llegadas: \(llegadas ? "sí" : "no")"
    }

    func preparar() {
        guard let ev = elegido else { return }
        guard let tok = sesion.tokenVigente else { volverALogin("La sesión venció: vuelve a iniciar sesión."); return }
        var args = ["preparar-auto", "--event", ev.id, "--label", etiqueta.trimmingCharacters(in: .whitespaces)]
        if todas { args.append("--todo") }
        else if !seleccion.isEmpty {
            // Sólo las elegidas a mano (las hijas van incluidas en su grupo).
            let ids = seleccion.filter { id in !(ev.actividades.first { $0.id == id }.map(padreElegido) ?? false) }
            args += ["--actividades", ids.sorted().joined(separator: ",")]
        }
        args.append(llegadas ? "--llegadas" : "--sin-llegadas")
        if reemplazarCopia { args.append("--reemplazar-copia") }
        if let f = nodoFallido {
            args += ["--reemplazar-nodo", f]
            if !args.contains("--reemplazar-copia") { args.append("--reemplazar-copia") }
        } else if reemplazarNodo, let v = ev.nodos.first(where: { $0.abierto && $0.id == motor.estado?.nodo?.id }) { args += ["--reemplazar-nodo", v.id] }
        paso = .trabajo; terminado = false
        motor.correr(args, titulo: "Preparando «\(ev.name)»", extra: ["NODO_API_TOKEN": tok, "NODO_API_BASE": SesionAPI.base]) { c in
            terminado = true
            if c == 0 { nodoFallido = nil }
            else if let f = motor.ultimoJSON?["failed_node_id"] as? String { nodoFallido = f }
            if c == 4 { volverALogin("adventist.club pidió iniciar sesión de nuevo (la sesión debe tener menos de 15 minutos).") }
        }
    }

    // MARK: Paso 4: trabajando / resumen

    var vistaTrabajo: some View {
        VStack(alignment: .leading, spacing: 10) {
            TituloPaso(n: 4, texto: motor.ocupado ? "Preparando…" : (motor.ultimoCodigo == 0 ? "Evento preparado" : "No se pudo preparar"))
            BarraProgreso()
            if terminado && !motor.ocupado {
                if motor.ultimoCodigo == 0 { ResumenPreparado(j: motor.ultimoJSON, actual: motor.estado?.certificado) }
                else {
                    Aviso(texto: motor.log.last(where: { $0.tipo == .error })?.texto ?? "Falló. Revisa el detalle.", luz: .rojo, icono: "xmark.octagon.fill")
                    HStack {
                        Button("‹ Cambiar el alcance") { paso = .alcance }
                        Button("Ver mis eventos") { Task { await cargarEventos() } }
                    }
                }
            }
            VistaLog(alto: terminado && motor.ultimoCodigo == 0 ? 120 : 230)
        }
    }

    // MARK: Subir

    var vistaSubir: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                TituloPaso(n: 2, texto: "Subir lo capturado en este nodo")
                Spacer()
                if eventos.count > 1 { Button("‹ Otro evento") { paso = .eventos; revisado = false } }
            }
            if let ev = elegido {
                Text("\(ev.name) · nodo «\(motor.estado?.nodo?.label ?? motor.cfg("NODO_LABEL"))»").font(.system(size: 15, weight: .semibold))
                let abiertos = ev.nodos.filter(\.abierto).count
                if abiertos > 1 { Text("Nodos abiertos en este evento: \(abiertos). La nube se libera cuando todos suban su final.").font(.system(size: 13)).foregroundStyle(.secondary) }
            }
            Picker("", selection: $final) {
                Text("Subir avance (sigo capturando)").tag(false)
                Text("Subir final (cierro este nodo)").tag(true)
            }.pickerStyle(.radioGroup).labelsHidden().font(.system(size: 15, weight: .semibold))
                .onChange(of: final) { _ in revisado = false }
            HStack(spacing: 12) {
                Button { revisar() } label: { Label("1. Revisar (no cambia nada)", systemImage: "eye").frame(minWidth: 230, minHeight: 34) }
                    .controlSize(.large)
                Button { confirmarSubir = true } label: {
                    Label(final ? "2. Subir final" : "2. Subir avance", systemImage: "icloud.and.arrow.up").frame(minWidth: 200, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).disabled(!revisado)
                Spacer()
            }.disabled(motor.ocupado)
            BarraProgreso()
            if !motor.ocupado, let j = motor.ultimoJSON, j["insert"] != nil || j["conflicts"] != nil {
                ReporteSubida(j: j, preferir: $preferir)
            }
            if !error.isEmpty { Aviso(texto: error, luz: .rojo, icono: "xmark.octagon.fill") }
            VistaLog(alto: 110)
        }
        .alert(final ? "¿Subir final y cerrar este nodo?" : "¿Subir avance?", isPresented: $confirmarSubir) {
            Button(final ? "Respaldar y subir final" : "Respaldar y subir avance") { subir() }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text(final
                 ? "Se hace un respaldo y se sube todo en una sola operación. Este nodo queda cerrado: ya no se captura en él."
                 : "Se hace un respaldo y se sube lo capturado hasta ahora. El nodo sigue abierto: puedes seguir capturando y volver a subir.")
        }
    }

    func argsSubir(seco: Bool) -> [String]? {
        guard let ev = elegido else { return nil }
        var a = ["subir-auto", "--event", ev.id, final ? "--final" : "--avance"]
        if seco { a.append("--dry-run") }
        if !preferir.isEmpty { a += ["--prefer", preferir] }
        return a
    }

    func revisar() {
        guard let tok = sesion.tokenVigente else { volverALogin("La sesión venció: vuelve a iniciar sesión."); return }
        guard let a = argsSubir(seco: true) else { return }
        error = ""
        motor.correr(a, titulo: "Revisando qué se subiría", extra: ["NODO_API_TOKEN": tok, "NODO_API_BASE": SesionAPI.base]) { c in
            revisado = (c == 0 || c == 2)
            if c == 4 { volverALogin("adventist.club pidió iniciar sesión de nuevo.") }
        }
    }

    func subir() {
        guard let tok = sesion.tokenVigente else { volverALogin("La sesión venció: vuelve a iniciar sesión."); return }
        guard let a = argsSubir(seco: false) else { return }
        motor.correr(a, titulo: final ? "Subiendo final" : "Subiendo avance", extra: ["NODO_API_TOKEN": tok, "NODO_API_BASE": SesionAPI.base]) { c in
            revisado = false
            if c == 4 { volverALogin("adventist.club pidió iniciar sesión de nuevo.") }
        }
    }

    // MARK: Pruebas (--prueba): llenar y pulsar sin ratón
    func pruebas(_ a: String) {
        if a.hasPrefix("auto:entrar:") {
            let p = a.dropFirst(12).split(separator: "|", maxSplits: 1).map(String.init)
            if p.count == 2 { correo = p[0]; clave = p[1]; entrar() }
        } else if a.hasPrefix("auto:codigo:") { codigo = String(a.dropFirst(12)); verificar() }
        else if a.hasPrefix("auto:evento:"), let i = Int(a.dropFirst(12)), i < eventos.count { elegir(eventos[i]) }
        else if a == "auto:todas" { todas = true }
        else if a.hasPrefix("auto:actividad:"), let i = Int(a.dropFirst(15)), i < arbol.count { seleccion.insert(arbol[i].0.id); todas = false }
        else if a == "auto:sin-llegadas" { llegadas = false }
        else if a.hasPrefix("auto:nombre:") { etiqueta = String(a.dropFirst(12)) }
        else if a == "auto:preparar" { preparar() }
        else if a == "auto:final" { final = true }
        else if a == "auto:avance" { final = false }
        else if a == "auto:revisar" { revisar() }
        else if a.hasPrefix("auto:preferir:") { preferir = String(a.dropFirst(14)) }
        else if a == "auto:subir" { subir() }
    }
}

struct FilaEvento: View {
    let ev: EventoAPI
    var body: some View {
        let abiertos = ev.nodos.filter(\.abierto)
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "calendar").font(.system(size: 24)).foregroundStyle(Color.accentColor).padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                Text(ev.name).font(.system(size: 17, weight: .semibold))
                Text([ev.fechas, ev.venue ?? "", ev.city ?? ""].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                if !abiertos.isEmpty {
                    Text("Nodos abiertos: " + abiertos.map { "«\($0.label)»" + ($0.activity_names.map { $0.isEmpty ? "" : " (\($0.joined(separator: ", ")))" } ?? "") }.joined(separator: ", "))
                        .font(.system(size: 13)).foregroundStyle(Luz.ambar.color).fixedSize(horizontal: false, vertical: true)
                } else if let l = ev.offline_lock {
                    Text("En nodo local: \(l.node_label ?? "—")").font(.system(size: 13)).foregroundStyle(Luz.ambar.color)
                }
            }
            Spacer()
            Text((ev.my_roles ?? []).map { $0 == "ADMIN" ? "Admin" : ($0 == "COORDINATOR" ? "Coordinación" : $0) }.joined(separator: " · "))
                .font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            Image(systemName: "chevron.right").foregroundStyle(.secondary).padding(.top, 4)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.gray.opacity(0.25)))
        .contentShape(Rectangle())
    }
}

/// Resumen de la última línea @@json de «preparar-auto».
struct ResumenPreparado: View {
    let j: [String: Any]?
    var actual: Estado.Cert? = nil
    func n(_ k: String) -> String { (j?[k] as? Int).map(String.init) ?? "—" }
    var body: some View {
        let cert = j?["certificate"] as? [String: Any]
        let estadoCert = (cert?["installed"] as? Bool) == true ? "ok" : (cert?["status"] as? String ?? (j?["certificate"] as? String ?? ""))
        let vence = (cert?["not_after"] as? String).map { String($0.prefix(10)) } ?? ""
        let actividades = (j?["activity_names"] as? [String]) ?? []
        let scope = j?["scope"] as? [String: Any]
        let todas = scope?["all"] as? Bool ?? false
        let llegadas = scope?["check_in"] as? Bool ?? false
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 14) {
                Dato(valor: n("clubs"), texto: "clubes")
                Dato(valor: n("accounts"), texto: "cuentas")
                Dato(valor: n("passes"), texto: "pases")
            }
            Text("Este nodo («\(j?["label"] as? String ?? "—")») captura: \(todas ? "todas las actividades" : (actividades.isEmpty ? "ninguna actividad" : actividades.joined(separator: ", "))) · llegadas: \(llegadas ? "sí" : "no")")
                .font(.system(size: 14, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
            if estadoCert == "ok" {
                Aviso(texto: "Certificado HTTPS instalado" + (vence.isEmpty ? "" : " (vence \(vence))"), luz: .verde, icono: "lock.shield.fill")
            } else if let c = actual, c.existe, c.tipo != "ca-local" {
                Aviso(texto: "Producción no mandó certificado: esta Mac sigue con el que ya tenía (vence \(c.vence)).", luz: .verde, icono: "lock.shield.fill")
            } else {
                Aviso(texto: "Sin certificado de producción: se usa uno local (plan B; cada teléfono instala el perfil). Mejor: Certificado › Let's Encrypt, o que el propietario lo configure en producción.", luz: .ambar)
            }
            Aviso(texto: "Siguiente: «Encender» (si no lo está) y probar con un teléfono.", luz: .verde, icono: "arrow.right.circle.fill")
        }
    }
}

struct Dato: View {
    let valor: String
    let texto: String
    var body: some View {
        VStack(spacing: 0) {
            Text(valor).font(.system(size: 30, weight: .heavy))
            Text(texto).font(.system(size: 13)).foregroundStyle(.secondary)
        }
        .frame(minWidth: 110).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
    }
}

/// Reporte de la subida (simulación o real), en palabras.
struct ReporteSubida: View {
    let j: [String: Any]
    @Binding var preferir: String
    static let tablas: [String: String] = [
        "event_checkins": "llegadas", "event_evaluations": "evaluaciones", "event_evaluation_reviews": "revisiones",
        "event_adjustments": "ajustes", "event_scores": "puntajes", "event_activity_results": "resultados",
        "audit_log": "bitácora",
    ]
    func lista(_ k: String) -> String {
        guard let d = j[k] as? [String: Any] else { return "" }
        return d.compactMap { (t, v) -> String? in
            guard let n = v as? Int, n > 0 else { return nil }
            return "\(n) \(ReporteSubida.tablas[t] ?? t)"
        }.sorted().joined(separator: ", ")
    }
    var body: some View {
        let seco = j["dry_run"] as? Bool ?? true
        let conflictos = (j["conflicts"] as? [[String: Any]]) ?? []
        let nuevos = lista("insert"), cambios = lista("update")
        VStack(alignment: .leading, spacing: 6) {
            Text(seco ? "Se subiría:" : ((j["already_applied"] as? Bool) == true ? "Ya estaba subido (sin cambios)." : "Subido:"))
                .font(.system(size: 15, weight: .bold))
            Text("Nuevos: \(nuevos.isEmpty ? "nada" : nuevos)").font(.system(size: 14))
            Text("Cambios: \(cambios.isEmpty ? "nada" : cambios)").font(.system(size: 14))
            if !conflictos.isEmpty {
                Aviso(texto: "\(conflictos.count) conflicto\(conflictos.count == 1 ? "" : "s"): algo cambió en la nube Y en este nodo. " + conflictos.prefix(3).map { c in [c["what"] as? String, c["detail"] as? String].compactMap { $0 }.joined(separator: ": ") }.joined(separator: " · "), luz: .ambar)
                Picker("Con los conflictos:", selection: $preferir) {
                    Text("Dejar pendientes").tag("")
                    Text("Gana el nodo (lo capturado aquí)").tag("node")
                    Text("Gana la nube").tag("prod")
                }.pickerStyle(.segmented)
            }
            if !seco {
                if (j["lock_released"] as? Bool) == true {
                    Aviso(texto: "Listo: la nube quedó liberada para este evento.", luz: .verde, icono: "checkmark.seal.fill")
                } else if let n = j["open_nodes"] as? Int {
                    Aviso(texto: "Subido. Nodos aún abiertos en este evento: \(n). La nube se libera cuando todos suban su final.", luz: .verde, icono: "checkmark.seal.fill")
                }
            }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
    }
}
