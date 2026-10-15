// «Red del evento»: EL lugar para todas las opciones de red. Elige una opción con botones grandes;
// cada una termina con el mismo «Revisar» (DNS vía el router, aislamiento, internet) y queda en
// verde «Listo» en la ventana principal (RED_MODO + RED_REVISION en config.env).
import SwiftUI

enum OpcionRed: String, CaseIterable, Identifiable {
    case glinet, publica, google, otro, lugar, varios
    var id: String { rawValue }
    var titulo: String {
        switch self {
        case .glinet: return "GL.iNet portátil (recomendado)"
        case .publica: return "Red pública del evento"
        case .google: return "Google Wifi / Nest Wifi"
        case .otro: return "Otro router con DNS personalizado"
        case .lugar: return "Red del lugar"
        case .varios: return "Varios nodos"
        }
    }
    var sub: String {
        switch self {
        case .glinet: return "El router de bolsillo. La app lo deja listo sola (una vez)."
        case .publica: return "Wi-Fi abierto para todos: sólo el sitio del evento, sin desvíos (GL.iNet, OpenWrt, MikroTik)."
        case .google: return "Tu Wifi de casa o de la iglesia: Google Home + el DNS de esta Mac."
        case .otro: return "MikroTik u OpenWrt: la app lo configura. Otros: pasos a mano."
        case .lugar: return "¿Puedo usar el Wi-Fi o el cable que ya hay? Revisa y recomienda."
        case .varios: return "Varios coordinadores, cada uno con su Mac, su router y sus actividades."
        }
    }
    var icono: String {
        switch self {
        case .glinet: return "wifi.router.fill"
        case .publica: return "person.wave.2.fill"
        case .google: return "dot.radiowaves.left.and.right"
        case .otro: return "server.rack"
        case .lugar: return "building.2"
        case .varios: return "person.3.fill"
        }
    }
    static func titulo(modo: String) -> String {
        OpcionRed(rawValue: modo)?.titulo.replacingOccurrences(of: " (recomendado)", with: "") ?? "Sin elegir"
    }
}

struct HojaRed: View {
    @Binding var abierta: Hoja?
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var opcion: OpcionRed?

    var body: some View {
        HojaMarco(titulo: "Red del evento", icono: "wifi.router", abierta: $abierta) {
            if let opcion {
                HStack {
                    Button { self.opcion = nil; motor.log = [] } label: { Label("Opciones de red", systemImage: "chevron.left") }
                        .disabled(motor.ocupado)
                    Text("·").foregroundStyle(.secondary)
                    Image(systemName: opcion.icono).foregroundStyle(Color.accentColor)
                    Text(opcion.titulo).font(.system(size: 17, weight: .bold))
                    Spacer()
                }
                ScrollViewReader { lector in
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        switch opcion {
                        case .glinet: RedGLiNet(pedirSecreto: pedirSecreto)
                        case .publica: RedPublica(pedirSecreto: pedirSecreto)
                        case .google: RedGoogle()
                        case .otro: RedOtro()
                        case .lugar: RedLugar()
                        case .varios: RedVarios()
                        }
                        if opcion != .varios { BloqueRevisar().id("revisar") }
                    }.padding(.trailing, 8)
                }
                .frame(minHeight: 520, maxHeight: 640)
                .onReceive(NotificationCenter.default.publisher(for: Prueba.interno)) { n in
                    if (n.object as? String) == "red:abajo" { lector.scrollTo("revisar", anchor: .bottom) }
                }
                }
            } else {
                elegir
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Prueba.interno)) { n in
            guard let a = n.object as? String, a.hasPrefix("red:") else { return }
            let v = String(a.dropFirst(4))
            if v == "abajo" { return }
            if v == "elegir" { opcion = nil } else if let o = OpcionRed(rawValue: v == "mi-red" ? "google" : v) { usar(o) }
        }
        .onAppear {
            if let p = Prueba.redPendiente, let o = OpcionRed(rawValue: p == "mi-red" ? "google" : p) { Prueba.redPendiente = nil; usar(o) }
        }
    }

    var elegir: some View {
        let actual = motor.cfg("RED_MODO")
        let listo = motor.cfg("RED_REVISION") == "ok"
        return VStack(alignment: .leading, spacing: 12) {
            Text("¿Cómo llegan los teléfonos a esta Mac? Elige una opción: cada una explica los pasos y termina con «Revisar».")
                .font(.system(size: 15)).fixedSize(horizontal: false, vertical: true)
            ForEach(OpcionRed.allCases) { o in
                Button { usar(o) } label: {
                    HStack(spacing: 16) {
                        Image(systemName: o.icono).font(.system(size: 30)).foregroundStyle(Color.accentColor).frame(width: 44)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(o.titulo).font(.system(size: 19, weight: .bold))
                            Text(o.sub).font(.system(size: 14)).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if o.rawValue == actual {
                            HStack(spacing: 6) {
                                Foco(luz: listo ? .verde : .ambar, tam: 14)
                                Text(listo ? "En uso · Listo" : "En uso · falta Revisar").font(.system(size: 13, weight: .bold))
                            }
                        }
                        Image(systemName: "chevron.right").foregroundStyle(.secondary)
                    }
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor)))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(o.rawValue == actual ? (listo ? Luz.verde.color : Luz.ambar.color) : Color.gray.opacity(0.25), lineWidth: o.rawValue == actual ? 2 : 1))
                    .contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
        }
    }

    func usar(_ o: OpcionRed) {
        opcion = o
        motor.log = []
        // «Varios nodos» es una guía: no cambia la red elegida.
        if o != .varios && motor.cfg("RED_MODO") != o.rawValue { motor.silencioso(["red-modo", o.rawValue]) }
    }
}

// MARK: - Piezas comunes

struct Pasos: View {
    let pasos: [String]
    var desde = 1
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(Array(pasos.enumerated()), id: \.offset) { i, p in
                HStack(alignment: .top, spacing: 10) {
                    Text("\(i + desde)").font(.system(size: 13, weight: .heavy)).foregroundStyle(.white)
                        .frame(width: 24, height: 24).background(Circle().fill(Color.accentColor))
                    Text(.init(p)).font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

/// IP y MAC de esta Mac, para copiarlas en el router.
struct DatosMac: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        let r = motor.estado?.red
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 22) {
                VStack(alignment: .leading) {
                    Text("IP de esta Mac").font(.system(size: 12)).foregroundStyle(.secondary)
                    Text(r?.ip.isEmpty == false ? r!.ip : "sin red").font(.system(size: 22, weight: .heavy, design: .monospaced)).textSelection(.enabled)
                }
                VStack(alignment: .leading) {
                    Text("MAC (\(r?.tipo == "ethernet" ? "cable" : "Wi-Fi"))").font(.system(size: 12)).foregroundStyle(.secondary)
                    Text(r?.mac ?? "—").font(.system(size: 18, weight: .bold, design: .monospaced)).textSelection(.enabled)
                }
                VStack(alignment: .leading) {
                    Text("Router").font(.system(size: 12)).foregroundStyle(.secondary)
                    Text(r?.gateway ?? "—").font(.system(size: 18, weight: .bold, design: .monospaced)).textSelection(.enabled)
                }
            }
            if r?.tipo == "wifi" && (r?.mac_privada ?? false) {
                Aviso(texto: "Por Wi-Fi con «Dirección privada»: en Ajustes del Sistema › Wi-Fi › (esta red) › Detalles, pon «Dirección privada de Wi-Fi» en FIJA o DESACTIVADA. Si rota, la reserva de IP deja de servir. Mejor: cable.", luz: .ambar)
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
    }
}

/// Encender/apagar el DNS de esta Mac (para «Mi red»).
struct BloqueDNS: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        let d = motor.estado?.dns
        let activo = d?.activo ?? false, responde = d?.responde ?? false
        HStack(spacing: 12) {
            Foco(luz: activo && responde ? .verde : (activo ? .rojo : .gris), tam: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(activo && responde ? "DNS de esta Mac: encendido" : (activo ? ((d?.interceptado ?? false) ? "Otra app intercepta el DNS (VPN, Little Snitch…): ciérrala" : "DNS de esta Mac: no responde") : "DNS de esta Mac: apagado"))
                    .font(.system(size: 15, weight: .bold))
                Text("Responde events/api/admin/media.adventist.club con esta Mac; lo demás lo pregunta a internet (si hay).")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            Spacer()
            if activo {
                Button("Apagar") { motor.correr(["dns-off"], titulo: "Apagando el DNS de esta Mac") }.controlSize(.large)
            } else {
                Button { motor.correr(["dns-on"], titulo: "Encendiendo el DNS de esta Mac") } label: {
                    Label("Encender el DNS de esta Mac", systemImage: "power").frame(minHeight: 30)
                }.controlSize(.large).buttonStyle(.borderedProminent)
            }
        }
        .disabled(motor.ocupado)
        .padding(12).background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
    }
}

/// El mismo «Revisar» al final de cada opción.
struct BloqueRevisar: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        let ok = motor.cfg("RED_REVISION") == "ok"
        let hay = !motor.cfg("RED_REVISION").isEmpty
        VStack(alignment: .leading, spacing: 10) {
            Divider()
            HStack(spacing: 14) {
                Button { motor.correr(["red-revisar"], titulo: "Revisando la red") } label: {
                    Label("Revisar", systemImage: "stethoscope").frame(minWidth: 140, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).disabled(motor.ocupado)
                if hay {
                    Foco(luz: ok ? .verde : .rojo, tam: 22)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(ok ? "Listo" : "Todavía no").font(.system(size: 20, weight: .heavy)).foregroundStyle(ok ? Luz.verde.color : Luz.rojo.color)
                        Text(motor.cfg("RED_REVISION_TXT")).font(.system(size: 13)).foregroundStyle(.secondary)
                    }
                } else {
                    Text("Revisa: el DNS (vía el router) dice que events.adventist.club es esta Mac, si los equipos se ven entre sí y si hay internet.")
                        .font(.system(size: 13)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
            BarraProgreso()
            VistaLog(alto: 150)
        }
    }
}

// MARK: - GL.iNet

struct RedGLiNet: View {
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var clave = ""
    @State private var ssid = ""
    @State private var conAdmin = false
    @State private var simular = false
    @State private var planVisto = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Con esta Mac conectada al GL.iNet Beryl AX (mejor por cable): el router siempre le dará la misma IP a esta Mac y responderá que events.adventist.club es esta Mac, también sin internet. Antes de aplicar verás exactamente qué cambia. El DNS de esta Mac no hace falta (se apaga).")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                GridRow {
                    Text("Contraseña de administración del router").font(.system(size: 14, weight: .semibold))
                    SecureField("la del panel del GL.iNet (no se guarda)", text: $clave).textFieldStyle(.roundedBorder)
                }
                GridRow {
                    Text("Nombre del Wi-Fi (opcional)").font(.system(size: 14, weight: .semibold))
                    TextField("vacío = no cambiar", text: $ssid).textFieldStyle(.roundedBorder)
                }
            }
            FilaSecreto(s: .wifi, pedir: pedirSecreto)
            HStack(spacing: 18) {
                Toggle("Incluir admin.adventist.club", isOn: $conAdmin)
                Toggle("Simular (sin router: sólo mostrar las órdenes)", isOn: $simular)
            }.font(.system(size: 14))
            HStack(spacing: 12) {
                Button { guardar(); motor.correr(["router-plan", "--tipo", "glinet"] + (simular ? ["--simular"] : []), titulo: "Leyendo el router", extra: ["NODO_ROUTER_PASS": clave]) { c in planVisto = (c == 0) } } label: {
                    Label("1. Ver qué cambiará", systemImage: "eye").frame(minWidth: 190, minHeight: 34)
                }.controlSize(.large).disabled(clave.isEmpty && !simular)
                Button { guardar(); motor.correr(["router-aplicar", "--tipo", "glinet"] + (simular ? ["--simular"] : []), titulo: "Configurando el router", extra: ["NODO_ROUTER_PASS": clave]) } label: {
                    Label("2. Aplicar", systemImage: "checkmark.seal").frame(minWidth: 140, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).disabled(!planVisto || (clave.isEmpty && !simular))
                Spacer()
            }.disabled(motor.ocupado)
            Text("Si algo falla, se puede hacer a mano en el panel del GL.iNet (README › «Configurar el router a mano»).")
                .font(.system(size: 12)).foregroundStyle(.secondary)
        }
        .onAppear {
            ssid = motor.cfg("WIFI_SSID")
            conAdmin = motor.cfg("DOMINIOS").contains("admin.adventist.club")
        }
        .onDisappear { clave = "" }
    }
    func guardar() {
        if ssid != motor.cfg("WIFI_SSID") { motor.guardarConfig("WIFI_SSID", ssid.trimmingCharacters(in: .whitespaces)) }
        let dom = "events.adventist.club api.adventist.club media.adventist.club" + (conAdmin ? " admin.adventist.club" : "")
        if dom != motor.cfg("DOMINIOS") { motor.guardarConfig("DOMINIOS", dom) }
    }
}

// MARK: - Red pública del evento

struct RedPublica: View {
    let pedirSecreto: (Secreto) -> Void
    @EnvironmentObject var motor: Motor
    @State private var tipo = "glinet"
    @State private var ipRouter = ""
    @State private var usuario = ""
    @State private var clave = ""
    @State private var ssid = ""
    @State private var abierta = true
    @State private var portal = "exito"
    @State private var soloMac = true
    @State private var simular = false
    @State private var planVisto = false
    @State private var confirmarQuitar = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Un Wi-Fi para todos (abierto o con una clave sencilla) que sólo abre el sitio del evento: cualquier otra página no carga, nadie puede usar otro DNS y los teléfonos no se ven entre sí. La app configura el router por SSH (GL.iNet, OpenWrt o MikroTik); antes de aplicar ves exactamente qué cambia, y «Quitar» lo deshace.")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            Aviso(texto: "Conexión recomendada: módem del lugar (si hay) → puerto WAN del GL.iNet → Wi-Fi del evento. Esta Mac por CABLE a un puerto LAN del GL.iNet (con el aislamiento, por Wi-Fi los teléfonos no la verían).", luz: .verde, icono: "cable.connector")
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 9) {
                GridRow {
                    Text("Router").font(.system(size: 14, weight: .semibold))
                    Picker("", selection: $tipo) {
                        Text("GL.iNet").tag("glinet"); Text("OpenWrt").tag("openwrt"); Text("MikroTik").tag("mikrotik")
                    }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 340)
                }
                if tipo != "glinet" {
                    GridRow {
                        Text("IP y usuario").font(.system(size: 14, weight: .semibold))
                        HStack {
                            TextField(motor.estado?.red.gateway ?? "192.168.88.1", text: $ipRouter).textFieldStyle(.roundedBorder).frame(maxWidth: 170)
                            TextField(tipo == "mikrotik" ? "admin" : "root", text: $usuario).textFieldStyle(.roundedBorder).frame(maxWidth: 140)
                        }
                    }
                }
                GridRow {
                    Text("Contraseña del router").font(.system(size: 14, weight: .semibold))
                    SecureField("la del panel (no se guarda)", text: $clave).textFieldStyle(.roundedBorder).frame(maxWidth: 340)
                }
                GridRow {
                    Text("Nombre del Wi-Fi").font(.system(size: 14, weight: .semibold))
                    TextField("p. ej. Camporee Universo de Dios", text: $ssid).textFieldStyle(.roundedBorder).frame(maxWidth: 340)
                }
                GridRow {
                    Text("Contraseña del Wi-Fi").font(.system(size: 14, weight: .semibold))
                    HStack {
                        Toggle("Abierta (sin contraseña)", isOn: $abierta).toggleStyle(.checkbox)
                        if !abierta { FilaSecreto(s: .wifi, pedir: pedirSecreto).frame(maxWidth: 420) }
                    }
                }
                GridRow {
                    Text("«¿Hay internet?»").font(.system(size: 14, weight: .semibold))
                    Picker("", selection: $portal) {
                        Text("Contestar «sí» (recomendado)").tag("exito")
                        Text("Página de bienvenida").tag("bienvenida")
                        Text("Nada").tag("ninguno")
                    }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 520)
                }
            }
            Text(textoPortal).font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 18) {
                Toggle("Sólo esta Mac sale a internet (para «Subir avance»)", isOn: $soloMac)
                Toggle("Simular (sin router)", isOn: $simular)
            }.font(.system(size: 14))
            HStack(spacing: 12) {
                Button { guardar(); motor.correr(["red-publica-plan", "--tipo", tipo] + (simular ? ["--simular"] : []), titulo: "Red pública: qué cambiará", extra: ["NODO_ROUTER_PASS": clave]) { c in planVisto = (c == 0) } } label: {
                    Label("1. Ver qué cambiará", systemImage: "eye").frame(minWidth: 180, minHeight: 34)
                }.controlSize(.large)
                Button { guardar(); motor.correr(["red-publica-aplicar", "--tipo", tipo] + (simular ? ["--simular"] : []), titulo: "Aplicando la red pública", extra: ["NODO_ROUTER_PASS": clave]) } label: {
                    Label("2. Aplicar", systemImage: "checkmark.seal").frame(minWidth: 130, minHeight: 34)
                }.controlSize(.large).buttonStyle(.borderedProminent).disabled(!planVisto || (clave.isEmpty && !simular))
                Spacer()
                Button("Quitar red pública…") { confirmarQuitar = true }.disabled(clave.isEmpty && !simular)
            }.disabled(motor.ocupado)
            if tipo == "mikrotik" {
                Text("MikroTik: la app deja DNS, firewall y DNS públicos para la Mac. El Wi-Fi (nombre, clave y «default-forwarding=no» / aislamiento) se pone a mano: cambia según el paquete de Wi-Fi de RouterOS.")
                    .font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            DisclosureGroup {
                Pasos(pasos: [
                    "**Módem de Telmex / Totalplay / Izzi:** casi nunca dejan cambiar el DNS ni bloquear sitios. Lo honesto: conecta su cable al **WAN del GL.iNet** y usa esta opción en el GL.iNet.",
                    "**Google Wifi / Nest Wifi:** DNS personalizado = esta Mac (opción «Google Wifi») y red de invitados NO (aísla también a la Mac). No hay lista de sitios permitidos ni bloqueo de otros DNS.",
                    "**UniFi / Omada:** reserva de IP, DNS de la red = esta Mac, «Client/Guest isolation» en el SSID del evento y una regla de firewall que sólo deje la Mac salir a internet. La lista de sitios permitidos no se puede hacer ahí: el DNS de esta Mac contesta los del evento y reenvía lo demás.",
                    "En todos: la Mac por cable, «Revisar» al final y probar con un iPhone y un Android.",
                ])
                .padding(.top, 6)
            } label: { Text("Otros routers (módem del lugar, Google Wifi, UniFi/Omada)").font(.system(size: 14, weight: .semibold)) }
        }
        .onAppear {
            let t = motor.cfg("ROUTER_TIPO")
            tipo = ["glinet", "openwrt", "mikrotik"].contains(t) ? t : "glinet"
            ipRouter = motor.cfg("ROUTER_IP_OTRO"); usuario = motor.cfg("ROUTER_USUARIO")
            ssid = motor.cfg("PUBLICA_SSID")
            abierta = motor.cfg("PUBLICA_ABIERTA") != "0"
            portal = motor.cfg("RED_PORTAL").isEmpty ? "exito" : motor.cfg("RED_PORTAL")
            soloMac = motor.cfg("PUBLICA_SOLO_MAC") != "0"
        }
        .onDisappear { clave = "" }
        .onReceive(NotificationCenter.default.publisher(for: Prueba.interno)) { n in
            guard let a = n.object as? String else { return }
            if a.hasPrefix("publica:ssid:") { ssid = String(a.dropFirst(13)) }
            else if a.hasPrefix("publica:portal:") { portal = String(a.dropFirst(15)) }
            else if a.hasPrefix("publica:tipo:") { tipo = String(a.dropFirst(13)) }
            else if a == "publica:simular" { simular = true; guardar(); motor.correr(["red-publica-aplicar", "--tipo", tipo, "--simular"], titulo: "Aplicando la red pública (simulación)") }
        }
        .alert("¿Quitar la red pública?", isPresented: $confirmarQuitar) {
            Button("Quitar") { guardar(); motor.correr(["red-publica-quitar", "--tipo", tipo] + (simular ? ["--simular"] : []), titulo: "Quitando la red pública", extra: ["NODO_ROUTER_PASS": clave]) }
            Button("Cancelar", role: .cancel) {}
        } message: { Text("El router vuelve a lo normal del nodo: reserva de IP y nombres del evento → esta Mac. Se quitan el bloqueo de sitios, el aislamiento y las reglas «nodo».") }
    }
    var textoPortal: String {
        switch portal {
        case "exito": return "Los teléfonos preguntan «¿hay internet?» al conectarse. Contestar «sí» (esta Mac responde por ellos) hace que iPhone y Android se queden en este Wi-Fi sin preguntar y no se vayan a los datos móviles. Android puede avisar «conectividad limitada» (su segunda prueba es HTTPS y no se puede imitar): elegir «Mantener conexión»."
        case "bienvenida": return "El teléfono abre su ventanita de «Iniciar sesión en la red» con un botón grande «Entrar a events.adventist.club». En iPhone esa ventanita es un mini navegador (sin cámara): el botón abre el sitio de verdad. Si el usuario la cierra, iPhone puede ofrecer «Usar otra red»."
        default: return "No se contesta: el teléfono dirá «sin internet». Android puede seguir usando los datos móviles para el sitio; hay que apagarlos."
        }
    }
    func guardar() {
        func g(_ k: String, _ v: String) { if motor.cfg(k) != v { motor.guardarConfig(k, v) } }
        g("ROUTER_TIPO", tipo)
        if tipo != "glinet" { g("ROUTER_IP_OTRO", ipRouter.trimmingCharacters(in: .whitespaces)); g("ROUTER_USUARIO", usuario.trimmingCharacters(in: .whitespaces)) }
        g("PUBLICA_SSID", ssid.trimmingCharacters(in: .whitespaces))
        g("PUBLICA_ABIERTA", abierta ? "1" : "0")
        g("RED_PORTAL", portal)
        g("PUBLICA_SOLO_MAC", soloMac ? "1" : "0")
    }
}

// MARK: - Google Wifi / Nest Wifi

struct RedGoogle: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        let ip = motor.estado?.red.ip ?? "<IP de esta Mac>"
        VStack(alignment: .leading, spacing: 12) {
            Aviso(texto: "Hazlo en casa (o en el lugar) CON internet, días antes. Los puntos Wifi necesitan corriente en el evento. Usa la red PRINCIPAL, no la de invitados (la de invitados aísla a los teléfonos).", luz: .ambar, icono: "house.fill")
            DatosMac()
            Pasos(pasos: [
                "Conecta esta Mac a la red **principal** del Google Wifi: mejor **por cable** al puerto de un punto Wifi. Por Wi-Fi: Ajustes del Sistema › Wi-Fi › (la red) › Detalles › **Dirección privada de Wi-Fi: Fija** (o Desactivada).",
                "En el teléfono, app **Google Home** › **Wi-Fi** › ⚙︎ **Configuración de red** › **Configuración avanzada de redes** › **Reservas de IP** › **+** › elige esta Mac (MAC **\(motor.estado?.red.mac ?? "—")**) → IP **\(ip)** › Guardar.",
                "Ahí mismo: **DNS** › **Personalizado** › Servidor DNS principal: **\(ip)**. Deja el secundario vacío (si la app lo exige, vuelve a poner **\(ip)**; NO pongas 8.8.8.8: los teléfonos se saltarían la Mac). Guardar.",
                "Ahí mismo: **IPv6** › **desactivado** (para que todos los teléfonos pregunten a este DNS).",
                "Abajo: **Encender el DNS de esta Mac** y luego **Revisar**.",
                "Prueba sin internet: desconecta el cable de internet (WAN) del punto Wifi principal, conecta un teléfono al Wi-Fi y abre https://events.adventist.club.",
            ])
            BloqueDNS()
            Aviso(texto: "Mientras el DNS del Google Wifi apunte a esta Mac, si la Mac se apaga o se duerme, nadie en esa red tendrá internet. Al terminar el evento: Google Home › DNS › Automático, y «Apagar» el DNS de esta Mac.", luz: .ambar, icono: "exclamationmark.triangle.fill")
        }
    }
}

// MARK: - Otro router

struct RedOtro: View {
    @EnvironmentObject var motor: Motor
    @State private var automatico = true
    @State private var tipo = "mikrotik"
    @State private var ipRouter = ""
    @State private var usuario = ""
    @State private var clave = ""
    @State private var simular = false
    @State private var planVisto = false
    var body: some View {
        let ip = motor.estado?.red.ip ?? "<IP de esta Mac>"
        VStack(alignment: .leading, spacing: 12) {
            Picker("", selection: $automatico) {
                Text("MikroTik u OpenWrt: configurarlo solo").tag(true)
                Text("Otro router: pasos a mano").tag(false)
            }.pickerStyle(.segmented).labelsHidden()
                .onChange(of: automatico) { v in guardarTipo(v ? tipo : "dns") }
            DatosMac()
            if automatico {
                Text("La app entra al router por SSH y deja: la misma IP siempre para esta Mac y events/api.adventist.club → esta Mac en el DNS del router. Nada más (Wi-Fi, firewall e internet no se tocan). Repetirlo no duplica nada.")
                    .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                    GridRow {
                        Text("Router").font(.system(size: 14, weight: .semibold))
                        Picker("", selection: $tipo) {
                            Text("MikroTik (RouterOS)").tag("mikrotik")
                            Text("OpenWrt").tag("openwrt")
                        }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 320)
                            .onChange(of: tipo) { v in guardarTipo(v); if usuario.isEmpty || usuario == "admin" || usuario == "root" { usuario = v == "mikrotik" ? "admin" : "root" } }
                    }
                    GridRow {
                        Text("IP del router").font(.system(size: 14, weight: .semibold))
                        TextField(motor.estado?.red.gateway ?? "192.168.88.1", text: $ipRouter).textFieldStyle(.roundedBorder).frame(maxWidth: 220)
                    }
                    GridRow {
                        Text("Usuario").font(.system(size: 14, weight: .semibold))
                        TextField(tipo == "mikrotik" ? "admin" : "root", text: $usuario).textFieldStyle(.roundedBorder).frame(maxWidth: 220)
                    }
                    GridRow {
                        Text("Contraseña").font(.system(size: 14, weight: .semibold))
                        SecureField("la del router (no se guarda)", text: $clave).textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    }
                }
                Toggle("Simular (sin router: sólo mostrar las órdenes)", isOn: $simular).font(.system(size: 14))
                HStack(spacing: 12) {
                    Button { guardarDatos(); motor.correr(["router-plan", "--tipo", tipo] + (simular ? ["--simular"] : []), titulo: "Leyendo el router", extra: ["NODO_ROUTER_PASS": clave]) { c in planVisto = (c == 0) } } label: {
                        Label("1. Ver qué cambiará", systemImage: "eye").frame(minWidth: 190, minHeight: 34)
                    }.controlSize(.large).disabled(clave.isEmpty && !simular)
                    Button { guardarDatos(); motor.correr(["router-aplicar", "--tipo", tipo] + (simular ? ["--simular"] : []), titulo: "Configurando el router", extra: ["NODO_ROUTER_PASS": clave]) } label: {
                        Label("2. Aplicar", systemImage: "checkmark.seal").frame(minWidth: 140, minHeight: 34)
                    }.controlSize(.large).buttonStyle(.borderedProminent).disabled(!planVisto || (clave.isEmpty && !simular))
                    Spacer()
                }.disabled(motor.ocupado)
                Text(tipo == "mikrotik" ? "MikroTik: SSH activo (IP › Services › ssh). Las clientes deben usar el router como DNS (IP › DHCP Server › Networks › DNS Servers = IP del router)." : "OpenWrt: SSH activo (System › Administration). Usa dnsmasq (lo normal).")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            } else {
                Pasos(pasos: [
                    "En el panel del router: **reserva de IP** (DHCP estático / «Address Reservation») para esta Mac: MAC **\(motor.estado?.red.mac ?? "—")** → IP **\(ip)**.",
                    "**DNS que reparte el router** (DHCP › DNS, «DNS personalizado» o «DNS server») → **\(ip)**. Sin DNS secundario público (si exige uno, pon también **\(ip)**).",
                    "Si hay **IPv6**, desactívalo (o que reparta el mismo DNS).",
                    "Usa la red **principal**: la de invitados suele aislar a los equipos.",
                    "Abajo: **Encender el DNS de esta Mac** y luego **Revisar**.",
                ])
                BloqueDNS()
                Aviso(texto: "Si la Mac se apaga, esa red se queda sin DNS. Al terminar, regresa el DNS del router a automático y apaga el DNS de esta Mac.", luz: .ambar)
            }
        }
        .onAppear {
            let t = motor.cfg("ROUTER_TIPO")
            automatico = t != "dns"
            tipo = (t == "openwrt" || t == "mikrotik") ? t : "mikrotik"
            ipRouter = motor.cfg("ROUTER_IP_OTRO")
            usuario = motor.cfg("ROUTER_USUARIO").isEmpty ? (tipo == "mikrotik" ? "admin" : "root") : motor.cfg("ROUTER_USUARIO")
            if t != tipo && automatico { guardarTipo(tipo) }
        }
        .onDisappear { clave = "" }
        .onReceive(NotificationCenter.default.publisher(for: Prueba.interno)) { n in
            guard let a = n.object as? String else { return }
            if a == "otro:manual" { automatico = false }
            else if a == "otro:mikrotik" { automatico = true; tipo = "mikrotik" }
            else if a == "otro:openwrt" { automatico = true; tipo = "openwrt" }
            else if a == "otro:simular" { simular = true; motor.correr(["router-aplicar", "--tipo", tipo, "--simular"], titulo: "Simulando") }
        }
    }
    func guardarTipo(_ t: String) { if motor.cfg("ROUTER_TIPO") != t { motor.guardarConfig("ROUTER_TIPO", t) } }
    func guardarDatos() {
        let i = ipRouter.trimmingCharacters(in: .whitespaces), u = usuario.trimmingCharacters(in: .whitespaces)
        if i != motor.cfg("ROUTER_IP_OTRO") { motor.guardarConfig("ROUTER_IP_OTRO", i) }
        if u != motor.cfg("ROUTER_USUARIO") { motor.guardarConfig("ROUTER_USUARIO", u) }
    }
}

// MARK: - Red del lugar

struct RedLugar: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Conecta esta Mac al Wi-Fi o al cable que ya hay en el lugar y pulsa el botón. Se revisa si hay internet, si los equipos se ven entre sí (las redes de invitados los aíslan) y si el DNS ya apunta a esta Mac.")
                .font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
            DatosMac()
            Button { motor.correr(["red-diagnostico"], titulo: "¿Puedo usar esta red?") } label: {
                Label("¿Puedo usar esta red?", systemImage: "questionmark.circle.fill").font(.system(size: 17, weight: .bold)).frame(minWidth: 260, minHeight: 40)
            }.controlSize(.large).buttonStyle(.borderedProminent).disabled(motor.ocupado)
            if !motor.ocupado, let j = motor.ultimoJSON, let reco = j["recomendacion"] as? String {
                let titulo = ["listo": "Puedes usar esta red", "glinet": "Usa tu GL.iNet", "nube": "Usa la nube", "dns_router": "Configura el DNS en tu router"][reco] ?? reco
                VStack(alignment: .leading, spacing: 6) {
                    Text(titulo).font(.system(size: 24, weight: .heavy))
                    Text(j["texto"] as? String ?? "").font(.system(size: 15)).fixedSize(horizontal: false, vertical: true)
                    Text("Internet: \((j["internet"] as? Bool) == true ? "sí" : "no") · otros equipos visibles: \(j["equipos"] as? Int ?? 0) · el router dice events.adventist.club → \((j["dns_events"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "(nada)")")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                }
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12).fill((reco == "listo" ? Luz.verde : Luz.ambar).color.opacity(0.13)))
            }
            Text("Con un GL.iNet: el cable de la red del lugar va al puerto **WAN** del GL.iNet (internet, si lo hay) y los teléfonos al Wi-Fi del GL.iNet. Así no importa cómo esté configurada la red del lugar.")
                .font(.system(size: 13)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Varios nodos

struct RedVarios: View {
    @EnvironmentObject var motor: Motor
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let n = motor.estado?.nodo, n.preparado {
                Aviso(texto: "Este nodo: «\(n.label ?? "—")» · captura: \(n.capturaTexto) · llegadas: \(n.check_in ? "sí" : "no") · \(n.estadoTexto)", luz: .verde, icono: "desktopcomputer")
            } else {
                Aviso(texto: "Esta Mac todavía no tiene evento: «Preparar evento» › Automático.", luz: .ambar, icono: "desktopcomputer")
            }
            Text("Varios coordinadores, varias zonas").font(.system(size: 18, weight: .bold))
            Pasos(pasos: [
                "Cada coordinador lleva **su Mac y su router** (un GL.iNet por zona). Pon un **Wi-Fi distinto** en cada uno (p. ej. «Camporee Zona A», «Camporee Zona B»).",
                "**La víspera**, cada uno: Preparar evento › Automático › elige **sus actividades** (las de otro nodo salen en gris). La mesa de llegadas puede estar en varios nodos.",
                "Cada router manda events.adventist.club a **su** Mac (opción «GL.iNet» en cada una). Los teléfonos de la zona A se conectan al Wi-Fi de la zona A.",
                "Un juez se queda en el Wi-Fi de su zona. Si cambia de zona, vuelve a iniciar sesión allí; si tiene capturas «por enviar», primero que regrese a su zona para mandarlas (en otro nodo esas actividades no se aceptan).",
                "**Durante:** si en algún momento hay señal, «Subir resultados» › **Subir avance** (el nodo sigue abierto).",
                "**Al terminar:** cada uno «Subir resultados» › **Subir final**. La nube se libera cuando todos los nodos subieron su final.",
                "El administrador ve los nodos del evento en la nube y puede **cerrar** uno que no volverá (lo no subido de ese nodo se pierde para la nube).",
            ])
        }
    }
}
