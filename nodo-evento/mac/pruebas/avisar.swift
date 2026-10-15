// Envía una orden de prueba a la app abierta con --prueba.  swift avisar.swift "hoja:qr"
import Foundation
DistributedNotificationCenter.default().postNotificationName(Notification.Name("club.adventist.nodo-camporee.prueba"),
    object: CommandLine.arguments[1], userInfo: nil, deliverImmediately: true)
