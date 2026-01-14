#!/usr/bin/env python3
"""
Agenda MCP URL Callback Handler

This script creates a macOS application that handles the agenda-mcp:// URL scheme.
When Agenda sends a callback to agenda-mcp://callback?..., this app receives it
and saves the data to a file for the MCP server to read.
"""

import os
import sys
import json
import time
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

try:
    from AppKit import NSApplication, NSObject, NSAppleEventManager
    from Foundation import NSAppleEventDescriptor
    from PyObjCTools import AppHelper
except ImportError:
    print("Error: pyobjc is required. Install with: pip install pyobjc-framework-Cocoa")
    sys.exit(1)


CALLBACK_DIR = Path.home() / ".agenda-mcp-callbacks"


class URLHandler(NSObject):
    """Handles incoming URL events from macOS."""

    def applicationWillFinishLaunching_(self, notification):
        """Register for URL events before the app finishes launching."""
        event_manager = NSAppleEventManager.sharedAppleEventManager()
        event_manager.setEventHandler_andSelector_forEventClass_andEventID_(
            self,
            "handleGetURLEvent:withReplyEvent:",
            int.from_bytes(b'GURL', 'big'),  # kInternetEventClass
            int.from_bytes(b'GURL', 'big'),  # kAEGetURL
        )

    def handleGetURLEvent_withReplyEvent_(self, event, reply_event):
        """Handle the URL event."""
        # Get the URL from the event
        url_descriptor = event.paramDescriptorForKeyword_(
            int.from_bytes(b'----', 'big')  # keyDirectObject
        )

        if url_descriptor:
            url_string = url_descriptor.stringValue()
            self.processURL_(url_string)

        # Quit after handling
        NSApplication.sharedApplication().terminate_(None)

    def processURL_(self, url_string):
        """Process the callback URL and save data to file."""
        try:
            # Ensure callback directory exists
            CALLBACK_DIR.mkdir(parents=True, exist_ok=True)

            # Parse the URL
            parsed = urlparse(url_string)
            params = parse_qs(parsed.query)

            # Decode parameters
            decoded_params = {}
            for key, values in params.items():
                decoded_values = [unquote(v) for v in values]
                decoded_params[key] = decoded_values[0] if len(decoded_values) == 1 else decoded_values

            # Create callback data
            callback_data = {
                "timestamp": time.time(),
                "url": url_string,
                "path": parsed.path,
                "params": decoded_params
            }

            # Generate unique callback ID
            callback_id = f"{int(time.time() * 1000)}"

            # Save to file
            callback_file = CALLBACK_DIR / f"callback-{callback_id}.json"
            with open(callback_file, 'w') as f:
                json.dump(callback_data, f, indent=2, ensure_ascii=False)

            # Also save to latest.json for easy access
            latest_file = CALLBACK_DIR / "latest.json"
            with open(latest_file, 'w') as f:
                json.dump(callback_data, f, indent=2, ensure_ascii=False)

            print(f"Callback saved to: {callback_file}")

        except Exception as e:
            print(f"Error processing URL: {e}")


def main():
    """Main entry point."""
    app = NSApplication.sharedApplication()
    delegate = URLHandler.alloc().init()
    app.setDelegate_(delegate)

    # Run the app
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
