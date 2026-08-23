# aerp/gui.py
# Copyright (C) 2025 Akita Engineering / www.akitaengineering.com

"""
Graphical User Interface for the Akita Emergency Response Plugin (AERP).
Provides a dark-themed dashboard using CustomTkinter.
"""

import threading
import queue
import logging
import re
import customtkinter as ctk
import meshtastic.serial_interface
import meshtastic.tcp_interface
from pubsub import pub
from typing import Any, Dict, Optional

from .plugin import AERP
from .config import ConfigManager
from .constants import CONFIG_ENABLED, CONFIG_PORT, CONFIG_INTERVAL

# Configure the fundamental palette
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")  # We will manually apply red/gray palettes

# UI Color Palette Constants
BG_BLACK = "#000000"
BG_GRAY_DARK = "#121212"
BG_GRAY_MID = "#202020"
BG_GRAY_LIGHT = "#333333"
TEXT_WHITE = "#FFFFFF"
TEXT_GRAY = "#B0B0B0"
ACCENT_RED = "#D32F2F"
ACCENT_RED_HOVER = "#B71C1C"

class UIQueueHandler(logging.Handler):
    """Logging handler that forwards logs to the Tkinter UI queue safely."""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put_nowait({"type": "log", "message": msg})
        except Exception:
            self.handleError(record)


class AERPApp(ctk.CTk):
    """Main Application Window for AERP."""
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config = config_manager
        
        # State
        self.aerp_instance: Optional[AERP] = None
        self.meshtastic_interface: Any = None
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=10_000)
        self.connection_thread: Optional[threading.Thread] = None
        self.log_handler: Optional[UIQueueHandler] = None
        self._closing = False
        
        # Setup Window
        self.title("AERP - Tactical Response Dashboard")
        self.geometry("1100x700")
        self.minsize(900, 600)
        self.configure(fg_color=BG_BLACK)
        self.protocol("WM_DELETE_WINDOW", self.handle_close)
        
        # Layout
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        
        self.build_sidebar()
        self.build_main_area()
        
        # periodic poller
        self.after(100, self.process_queue)
        
        # Hook up CLI-level pubsub listeners directed to this instance
        pub.subscribe(self.on_receive, "meshtastic.receive")
        pub.subscribe(self.on_connection_est, "meshtastic.connection.established")
        pub.subscribe(self.on_connection_lost, "meshtastic.connection.lost")

    # --- UI Builders ---
    
    def build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=280, corner_radius=0, fg_color=BG_GRAY_DARK)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(5, weight=1) # Push logs to bottom
        
        # Brand Header
        self.brand_lbl = ctk.CTkLabel(self.sidebar, text="A E R P", font=ctk.CTkFont(size=28, weight="bold"), text_color=ACCENT_RED)
        self.brand_lbl.grid(row=0, column=0, padx=20, pady=(20, 0), sticky="w")
        self.sub_brand_lbl = ctk.CTkLabel(self.sidebar, text="Akita Emergency Response", font=ctk.CTkFont(size=12), text_color=TEXT_GRAY)
        self.sub_brand_lbl.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")
        
        # Connection Controls
        self.conn_frame = ctk.CTkFrame(self.sidebar, fg_color=BG_GRAY_MID)
        self.conn_frame.grid(row=2, column=0, padx=20, pady=10, sticky="ew")
        
        self.conn_lbl = ctk.CTkLabel(self.conn_frame, text="Connection", font=ctk.CTkFont(weight="bold"))
        self.conn_lbl.pack(pady=(10,5), padx=10, anchor="w")
        
        self.entry_device = ctk.CTkEntry(self.conn_frame, placeholder_text="/dev/ttyUSB0 or IP", fg_color=BG_GRAY_DARK, border_color=BG_GRAY_LIGHT)
        self.entry_device.pack(pady=5, padx=10, fill="x")
        
        self.btn_connect = ctk.CTkButton(self.conn_frame, text="CONNECT RADIO", fg_color=BG_GRAY_LIGHT, hover_color=BG_GRAY_DARK, text_color=TEXT_WHITE, command=self.handle_connect)
        self.btn_connect.pack(pady=(5, 10), padx=10, fill="x")
        
        # Local Status Panel
        self.status_frame = ctk.CTkFrame(self.sidebar, fg_color=BG_GRAY_MID)
        self.status_frame.grid(row=3, column=0, padx=20, pady=10, sticky="ew")
        
        self.status_title = ctk.CTkLabel(self.status_frame, text="Local Node", font=ctk.CTkFont(weight="bold"))
        self.status_title.pack(pady=(10, 5), padx=10, anchor="w")
        
        self.lbl_node_id = ctk.CTkLabel(self.status_frame, text="Node: Unknown", text_color=TEXT_GRAY)
        self.lbl_node_id.pack(pady=2, padx=10, anchor="w")
        
        self.lbl_state = ctk.CTkLabel(self.status_frame, text="State: Disconnected", text_color=TEXT_GRAY)
        self.lbl_state.pack(pady=2, padx=10, anchor="w")

        # Config Summary
        self.cfg_frame = ctk.CTkFrame(self.sidebar, fg_color=BG_GRAY_MID)
        self.cfg_frame.grid(row=4, column=0, padx=20, pady=10, sticky="ew")
        cfg_str = f"Port: {self.config.get(CONFIG_PORT)} | Interval: {self.config.get(CONFIG_INTERVAL)}s"
        self.lbl_cfg = ctk.CTkLabel(self.cfg_frame, text=cfg_str, text_color=TEXT_GRAY)
        self.lbl_cfg.pack(pady=10, padx=10, anchor="w")
    
    def build_main_area(self):
        self.main_area = ctk.CTkFrame(self, fg_color=BG_BLACK, corner_radius=0)
        self.main_area.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_area.grid_rowconfigure(1, weight=1)
        self.main_area.grid_columnconfigure(0, weight=1)
        
        # Large Control Buttons
        self.control_frame = ctk.CTkFrame(self.main_area, fg_color=BG_BLACK)
        self.control_frame.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        self.control_frame.grid_columnconfigure(0, weight=1)
        self.control_frame.grid_columnconfigure(1, weight=1)
        
        self.btn_start = ctk.CTkButton(self.control_frame, text="BROADCAST EMERGENCY", font=ctk.CTkFont(size=22, weight="bold"),
                                       fg_color=ACCENT_RED, hover_color=ACCENT_RED_HOVER, text_color=TEXT_WHITE,
                                       height=70, corner_radius=8, command=self.handle_start_emergency)
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.btn_stop = ctk.CTkButton(self.control_frame, text="SEND ALL-CLEAR", font=ctk.CTkFont(size=22, weight="bold"),
                                      fg_color=BG_GRAY_LIGHT, hover_color=BG_GRAY_MID, text_color=TEXT_WHITE,
                                      height=70, corner_radius=8, command=self.handle_stop_emergency)
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        # Dashboard Tabs
        self.tabs = ctk.CTkTabview(self.main_area, corner_radius=8, fg_color=BG_GRAY_DARK, 
                                   segmented_button_fg_color=BG_GRAY_MID, segmented_button_selected_color=BG_GRAY_LIGHT, segmented_button_selected_hover_color=BG_GRAY_LIGHT)
        self.tabs.grid(row=1, column=0, sticky="nsew")
        
        self.tab_incidents = self.tabs.add("Active Incidents")
        self.tab_acks = self.tabs.add("Acknowledgements")
        self.tab_logs = self.tabs.add("System Log")
        
        # Active Incidents Layout
        self.tab_incidents.grid_columnconfigure(0, weight=1)
        self.tab_incidents.grid_rowconfigure(0, weight=1)
        self.incidents_scroll = ctk.CTkScrollableFrame(self.tab_incidents, fg_color="transparent")
        self.incidents_scroll.grid(row=0, column=0, sticky="nsew")
        self.incident_widgets = {} # Map node_id -> widget
        
        # Acks Layout
        self.tab_acks.grid_columnconfigure(0, weight=1)
        self.tab_acks.grid_rowconfigure(0, weight=1)
        self.acks_txt = ctk.CTkTextbox(self.tab_acks, fg_color=BG_GRAY_MID, text_color=TEXT_WHITE, font=ctk.CTkFont(family="Courier", size=14))
        self.acks_txt.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.acks_txt.insert("0.0", "Waiting for acknowledgements...\n")
        self.acks_txt.configure(state="disabled")

        # Logs Layout
        self.tab_logs.grid_columnconfigure(0, weight=1)
        self.tab_logs.grid_rowconfigure(0, weight=1)
        self.log_txt = ctk.CTkTextbox(self.tab_logs, fg_color=BG_GRAY_MID, text_color=TEXT_GRAY, font=ctk.CTkFont(family="Courier", size=12))
        self.log_txt.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.log_txt.configure(state="disabled")

    # --- Actions ---
    
    def handle_connect(self):
        if self.connection_thread and self.connection_thread.is_alive():
            return
        self.btn_connect.configure(state="disabled", text="CONNECTING...")
        target = self.entry_device.get().strip()
        
        # Spin up a background thread to prevent UI freezing
        self.connection_thread = threading.Thread(target=self._bg_connect, args=(target,), daemon=True)
        self.connection_thread.start()

    def _bg_connect(self, target):
        self.ui_queue.put({"type": "log", "message": f"Establishing connection to {target if target else 'auto-detect'}..."})
        try:
            old_plugin = self.aerp_instance
            old_interface = self.meshtastic_interface
            self.aerp_instance = None
            self.meshtastic_interface = None
            if old_plugin:
                old_plugin.close(send_clear=True)
            if old_interface:
                try:
                    old_interface.close()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Previous interface reported an error while closing.",
                        exc_info=True,
                    )

            if target.lower().startswith("tcp://"):
                hostname = target[6:].strip()
                if not hostname:
                    raise ValueError("tcp:// must be followed by a hostname or IP address")
                interface = meshtastic.tcp_interface.TCPInterface(hostname=hostname)
            elif target.lower().startswith("serial://"):
                device_path = target[9:].strip()
                if not device_path:
                    raise ValueError("serial:// must be followed by a device path")
                interface = meshtastic.serial_interface.SerialInterface(devPath=device_path)
            elif target and (target.startswith(("/", "\\\\")) or re.fullmatch(r"COM\d+", target, re.IGNORECASE)):
                interface = meshtastic.serial_interface.SerialInterface(devPath=target)
            elif target:
                interface = meshtastic.tcp_interface.TCPInterface(hostname=target)
            else:
                interface = meshtastic.serial_interface.SerialInterface()
                
            self.meshtastic_interface = interface
            self.aerp_instance = AERP(self.meshtastic_interface, self.config)

            if self.config.get(CONFIG_ENABLED, False):
                if not self.aerp_instance.start_emergency():
                    self.ui_queue.put({"type": "log", "message": "Configured auto-start failed; see the log for details."})
            self.ui_queue.put({"type": "connection_success"})
        except Exception as e:
            if self.meshtastic_interface:
                try:
                    self.meshtastic_interface.close()
                except Exception:
                    logging.getLogger(__name__).debug("Failed to close interface after connection error.", exc_info=True)
            self.meshtastic_interface = None
            self.aerp_instance = None
            self.ui_queue.put({"type": "log", "message": f"Connection failed: {e}"})
            self.ui_queue.put({"type": "connection_failed"})

    def handle_start_emergency(self):
        if self.aerp_instance:
            if self.aerp_instance.start_emergency():
                self.ui_queue.put({"type": "log", "message": "EMERGENCY BROADCAST INITIATED"})
                self.btn_start.configure(fg_color="#ff0000", border_width=2, border_color="#ffffff")
        else:
            self.ui_queue.put({"type": "log", "message": "Cannot start: Not connected to radio."})

    def handle_stop_emergency(self):
        if self.aerp_instance:
            if self.aerp_instance.emergency_active:
                self.aerp_instance.stop_emergency(send_clear=True)
                self.ui_queue.put({"type": "log", "message": "BROADCAST STOPPED; ALL-CLEAR TRANSMISSION ATTEMPTED"})
            elif self.aerp_instance.last_sent_emergency_id:
                if self.aerp_instance.send_clear_message(self.aerp_instance.last_sent_emergency_id):
                    self.ui_queue.put({"type": "log", "message": "ALL-CLEAR QUEUED (No active broadcast)"})
            self.btn_start.configure(fg_color=ACCENT_RED, border_width=0)
        else:
            self.ui_queue.put({"type": "log", "message": "Cannot stop: Not connected to radio."})

    # --- Event Queues & PubSub Listeners ---
    
    def on_receive(self, packet, interface):
        # We bounce the packet to AERP on the mesh thread, then fetch state changes to UI
        if self.aerp_instance and interface is self.meshtastic_interface:
            self.aerp_instance.handle_incoming(packet, interface)
            self.ui_queue.put({"type": "sync_status"})
            
    def on_connection_est(self, interface):
        if self.aerp_instance and interface is self.meshtastic_interface:
            self.aerp_instance.on_connection_change(interface, True)
            self.ui_queue.put({"type": "sync_status"})

    def on_connection_lost(self, interface):
        if self.aerp_instance and interface is self.meshtastic_interface:
            self.aerp_instance.on_connection_change(interface, False)
            self.ui_queue.put({"type": "connection_lost"})

    def process_queue(self):
        """Called repeatedly by Tkinter's mainloop to process threads safely."""
        while not self.ui_queue.empty():
            try:
                event = self.ui_queue.get_nowait()
                rtype = event.get("type")
                
                if rtype == "log":
                    self.log_txt.configure(state="normal")
                    self.log_txt.insert("end", event["message"] + "\n")
                    self.log_txt.see("end")
                    self.log_txt.configure(state="disabled")
                    
                elif rtype == "connection_success":
                    self.btn_connect.configure(state="normal", text="CONNECTED", fg_color="green")
                    self.lbl_state.configure(text="State: Connected", text_color="green")
                    if self.aerp_instance and self.aerp_instance.my_node_id != "Unknown":
                        self.lbl_node_id.configure(text=f"Node: {self.aerp_instance.my_node_id}", text_color=TEXT_WHITE)
                        
                elif rtype == "connection_failed":
                    self.btn_connect.configure(state="normal", text="CONNECT RADIO", fg_color=BG_GRAY_LIGHT)
                    self.lbl_state.configure(text="State: Disconnected", text_color="red")

                elif rtype == "connection_lost":
                    self.btn_connect.configure(state="normal", text="RECONNECT", fg_color=BG_GRAY_LIGHT)
                    self.lbl_state.configure(text="State: Disconnected", text_color="red")
                    self.lbl_node_id.configure(text="Node: Unknown", text_color=TEXT_GRAY)
                    
                elif rtype == "sync_status":
                    self._sync_ui_with_aerp()
                    
            except queue.Empty:
                break
                
        # Keep polling a fresh status sync every second to catch background cleanup changes
        self._sync_ui_timer = getattr(self, '_sync_ui_timer', 0)
        self._sync_ui_timer += 1
        if self._sync_ui_timer >= 10:  # Every 10 ticks (1s)
            self._sync_ui_timer = 0
            if self.aerp_instance:
                self._sync_ui_with_aerp()
                
        if not self._closing:
            self.after(100, self.process_queue)

    def _sync_ui_with_aerp(self):
        """Update UI to match AERP internal dictionary state."""
        if not self.aerp_instance: return
        
        status = self.aerp_instance.get_status()
        if status.get("emergency_active"):
            self.lbl_state.configure(text="State: EMERGENCY ACTIVE", text_color=ACCENT_RED)
        elif self.aerp_instance.my_node_num is not None:
            self.lbl_state.configure(text="State: Connected", text_color="green")
        
        # Node ID
        if status.get("my_node_id") and self.lbl_node_id.cget("text") == "Node: Unknown":
            self.lbl_node_id.configure(text=f"Node: {status['my_node_id']}", text_color=TEXT_WHITE)
            
        # Update Emergencies Card List
        emergencies = status.get("active_received_emergencies", {})
        
        # Remove old incidents
        to_remove = [
            nid
            for nid in self.incident_widgets
            if nid != "empty_lbl" and nid not in emergencies
        ]
        for nid in to_remove:
            self.incident_widgets[nid].destroy()
            del self.incident_widgets[nid]
            
        # Update or create new incidents
        for node_id, info in emergencies.items():
            msg = info.get('message', 'No Message')
            gps = info.get('gps') or {}
            lat = gps.get('latitude')
            lon = gps.get('longitude')
            position = f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else "Not available"
            batt = info.get('battery')
            battery = f"{batt}%" if batt is not None else "N/A"
            seen = info.get('last_seen', 'Unknown')
            
            card_text = f"NODE: {node_id}\nLST SEEN: {seen} | BATT: {battery}\nPOS: {position}\nMSG: {msg}"
            
            if node_id not in self.incident_widgets:
                card = ctk.CTkFrame(self.incidents_scroll, fg_color=ACCENT_RED, corner_radius=6)
                card.pack(fill="x", pady=5, padx=5)
                lbl = ctk.CTkLabel(card, text=card_text, font=ctk.CTkFont(weight="bold"), text_color=TEXT_WHITE, justify="left", padding=10)
                lbl.pack(anchor="w")
                self.incident_widgets[node_id] = card
                # Also store a reference to the label so we can update it
                card._lbl_ref = lbl
            else:
                self.incident_widgets[node_id]._lbl_ref.configure(text=card_text)
                
        # Handle empty State
        if not emergencies and "empty_lbl" not in self.incident_widgets:
            card = ctk.CTkLabel(self.incidents_scroll, text="No Active Incidents Detected.", text_color=TEXT_GRAY)
            card.pack(pady=20)
            self.incident_widgets["empty_lbl"] = card
        elif emergencies and "empty_lbl" in self.incident_widgets:
            self.incident_widgets["empty_lbl"].destroy()
            del self.incident_widgets["empty_lbl"]

        # Update Acknowledgements
        acks = status.get("active_acknowledgements", {})
        self.acks_txt.configure(state="normal")
        self.acks_txt.delete("0.0", "end")
        if not acks:
            self.acks_txt.insert("end", "No acknowledgements received for current/latest event.\n")
        else:
            self.acks_txt.insert("end", "--- ACKNOWLEDGED BY ---\n")
            for node_id, timestamp in acks.items():
                self.acks_txt.insert("end", f"[{timestamp}] Node {node_id}\n")
        self.acks_txt.configure(state="disabled")

    def handle_close(self):
        """Release subscriptions, workers, and the radio before closing."""
        if self._closing:
            return
        self._closing = True
        for callback, topic in (
            (self.on_receive, "meshtastic.receive"),
            (self.on_connection_est, "meshtastic.connection.established"),
            (self.on_connection_lost, "meshtastic.connection.lost"),
        ):
            try:
                pub.unsubscribe(callback, topic)
            except Exception:
                logging.getLogger(__name__).debug("GUI callback was already unsubscribed.")
        if self.aerp_instance:
            self.aerp_instance.close(send_clear=True)
            self.aerp_instance = None
        if self.meshtastic_interface:
            try:
                self.meshtastic_interface.close()
            except Exception:
                logging.getLogger(__name__).exception("Failed to close the Meshtastic interface.")
            self.meshtastic_interface = None
        if self.log_handler:
            logging.getLogger().removeHandler(self.log_handler)
        self.destroy()

def main():
    # Provide a CLI argument to specify config path just like the cli.py
    import argparse
    parser = argparse.ArgumentParser(description="AERP Graphical Interface")
    parser.add_argument("--config", default="config/aerp_config.json", help="Path to config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Create root logger and give it our UI Queue Handler once UI boots
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    # Initialize configs
    cm = ConfigManager(args.config)
    
    app = AERPApp(cm)
    
    # Send logs to UI
    qh = UIQueueHandler(app.ui_queue)
    logging.getLogger().addHandler(qh)
    app.log_handler = qh
    app.ui_queue.put({"type": "log", "message": "AERP Dashboard Initialized. Please connect your radio."})
    
    try:
        app.mainloop()
    finally:
        if not app._closing:
            app.handle_close()

if __name__ == "__main__":
    main()
