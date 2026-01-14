import Cocoa
import Foundation

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        // Register for URL events
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleURLEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    @objc func handleURLEvent(_ event: NSAppleEventDescriptor, withReplyEvent replyEvent: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue else {
            NSApplication.shared.terminate(nil)
            return
        }

        processURL(urlString)
        NSApplication.shared.terminate(nil)
    }

    func processURL(_ urlString: String) {
        // Create callback directory
        let callbackDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".agenda-mcp-callbacks")

        do {
            try FileManager.default.createDirectory(at: callbackDir, withIntermediateDirectories: true)
        } catch {
            print("Error creating directory: \(error)")
            return
        }

        // Parse URL
        guard let url = URL(string: urlString),
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            print("Invalid URL: \(urlString)")
            return
        }

        // Extract parameters
        var params: [String: Any] = [:]
        if let queryItems = components.queryItems {
            for item in queryItems {
                if let value = item.value {
                    // URL decode the value
                    let decodedValue = value.removingPercentEncoding ?? value
                    params[item.name] = decodedValue
                }
            }
        }

        // Create callback data
        let callbackData: [String: Any] = [
            "timestamp": Date().timeIntervalSince1970,
            "url": urlString,
            "path": components.path,
            "params": params
        ]

        // Generate unique callback ID
        let callbackId = String(format: "%.0f", Date().timeIntervalSince1970 * 1000)

        // Save to file
        let callbackFile = callbackDir.appendingPathComponent("callback-\(callbackId).json")
        let latestFile = callbackDir.appendingPathComponent("latest.json")

        do {
            let jsonData = try JSONSerialization.data(withJSONObject: callbackData, options: .prettyPrinted)

            try jsonData.write(to: callbackFile)
            try jsonData.write(to: latestFile)

            print("Callback saved to: \(callbackFile.path)")
        } catch {
            print("Error saving callback: \(error)")
        }
    }
}

// Main
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
