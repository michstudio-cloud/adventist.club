// Dibuja el ícono de «Nodo Camporee» (1024×1024 PNG): cielo nocturno, carpa y ondas de señal.
// Uso: swift icono.swift salida.png
import AppKit

let salida = CommandLine.arguments.dropFirst().first ?? "icono.png"
let n: CGFloat = 1024
let img = NSImage(size: NSSize(width: n, height: n))
img.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext

// Fondo: cuadrado redondeado estilo macOS con degradado.
let marco = CGRect(x: 100, y: 100, width: 824, height: 824)
let fondo = CGPath(roundedRect: marco, cornerWidth: 185, cornerHeight: 185, transform: nil)
ctx.saveGState()
ctx.addPath(fondo); ctx.clip()
let colores = [NSColor(calibratedRed: 0.07, green: 0.15, blue: 0.36, alpha: 1).cgColor,
               NSColor(calibratedRed: 0.05, green: 0.42, blue: 0.47, alpha: 1).cgColor] as CFArray
let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colores, locations: [0, 1])!
ctx.drawLinearGradient(grad, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])
// Estrellas
ctx.setFillColor(NSColor.white.withAlphaComponent(0.85).cgColor)
for (x, y, r) in [(230.0, 800.0, 7.0), (330, 860, 5), (780, 830, 6), (850, 720, 4), (190, 660, 4), (700, 880, 4)] {
    ctx.fillEllipse(in: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
}
// Suelo
ctx.setFillColor(NSColor(calibratedRed: 0.13, green: 0.55, blue: 0.33, alpha: 1).cgColor)
ctx.fill(CGRect(x: 100, y: 100, width: 824, height: 170))
// Carpa
ctx.setFillColor(NSColor(calibratedRed: 0.99, green: 0.76, blue: 0.2, alpha: 1).cgColor)
ctx.move(to: CGPoint(x: 512, y: 600)); ctx.addLine(to: CGPoint(x: 760, y: 250)); ctx.addLine(to: CGPoint(x: 264, y: 250)); ctx.closePath(); ctx.fillPath()
ctx.setFillColor(NSColor(calibratedRed: 0.55, green: 0.3, blue: 0.08, alpha: 1).cgColor)
ctx.move(to: CGPoint(x: 512, y: 470)); ctx.addLine(to: CGPoint(x: 590, y: 250)); ctx.addLine(to: CGPoint(x: 434, y: 250)); ctx.closePath(); ctx.fillPath()
// Ondas de señal sobre la carpa
ctx.setStrokeColor(NSColor.white.cgColor)
ctx.setLineCap(.round)
for (i, r) in [70.0, 135, 200].enumerated() {
    ctx.setLineWidth(CGFloat(34 - i * 4))
    ctx.addArc(center: CGPoint(x: 512, y: 610), radius: CGFloat(r), startAngle: .pi * 0.25, endAngle: .pi * 0.75, clockwise: false)
    ctx.strokePath()
}
ctx.setFillColor(NSColor.white.cgColor)
ctx.fillEllipse(in: CGRect(x: 488, y: 586, width: 48, height: 48))
ctx.restoreGState()
img.unlockFocus()

let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: salida))
print("ícono: \(salida)")
