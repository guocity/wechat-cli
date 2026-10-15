import AppKit
import PDFKit
import Foundation
// Render the first page of an already retrieved PDF. No network or external app.
guard CommandLine.arguments.count == 3,
      let document=PDFDocument(url:URL(fileURLWithPath:CommandLine.arguments[1])),
      let page=document.page(at:0) else { exit(1) }
let bounds=page.bounds(for:.mediaBox)
guard bounds.width>0,bounds.height>0,bounds.width.isFinite,bounds.height.isFinite else { exit(1) }
let image=page.thumbnail(of:NSSize(width:320,height:420),for:.mediaBox)
guard let tiff=image.tiffRepresentation,let bitmap=NSBitmapImageRep(data:tiff),
      let png=bitmap.representation(using:.png,properties:[:]) else { exit(1) }
do {try png.write(to:URL(fileURLWithPath:CommandLine.arguments[2]),options:.atomic)} catch {exit(1)}
