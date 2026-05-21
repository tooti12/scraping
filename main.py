# main.py
import time
import itertools
import json
import os
from datetime import datetime
from typing import Dict, Any, Optional

from api_client import APIClient
from auth_handler import AuthHandler
from browser_client import BrowserClient
from config import COUNTRY_CONFIG
from notification_handler import SMSNotifier
import screen_keeper


class VfsScraper:
    def __init__(self, country, email, password, persist_session=True):
        self.country = country
        self.email = email
        self.password = password
        self.config = COUNTRY_CONFIG[country]
        self.notifier = SMSNotifier()
        self.auth_token = None
        self.start_time = time.time()
        self.max_runtime = 30 * 60  # seconds
        self.persist_session = persist_session
        self.max_retries = 3
        self.retry_count = 0
        self.session_valid = True
        
        # Create logs directory
        os.makedirs("logs", exist_ok=True)
        self.log_file = f"logs/nld_appointments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
    def log_appointment_data(self, data: Dict[str, Any]):
        """Log appointment data to file"""
        try:
            with open(self.log_file, "a") as f:
                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "email": self.email,
                    "country": self.country,
                    "data": data
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            print(f"Error logging appointment data: {e}")

    def start_monitoring(self):
        with BrowserClient(self.country, proxy=False) as browser:
            # Check if we have a valid session from previous run
            print("Found valid session, attempting to reuse...")
            self.auth_token = browser.get_auth_token()
            if self.auth_token:
                    print("Session reused successfully!")
            else:
                    print("Session invalid, authenticating fresh...")
                    self.auth_token = AuthHandler(
                        self.country, self.email, self.password, browser
                    ).authenticate()
                
            if self.auth_token is None:
                print("Auth token is none. Exiting.")
                return
                
            browser.sb.sleep(5)
            browser.sb.driver.uc_click("button.mat-btn-lg")
            browser.sb.sleep(5)
            
            print(f"Starting monitoring for {self.country} with account {self.email}")
            print(f"Session persistence: {self.persist_session}")
            print(f"Max runtime: {self.max_runtime//60} minutes")
            
            while True:
                if time.time() - self.start_time > self.max_runtime:
                    print("Reached 30-minute limit. Exiting...")
                    return
                    
                try:
                    response = browser.call_check_slot(
                        jwt_token=self.auth_token, login_user=self.email
                    )
                    
                    # Log the raw response
                    self.log_appointment_data({
                        "type": "api_response",
                        "response": response
                    })
                    
                    # Handle comprehensive response analysis
                    self._handle_comprehensive_response(response, browser)
                    
                    # Determine next action based on analysis
                    analysis = response.get("analysis", {})
                    action = analysis.get("action", "wait")
                    
                    if action == "book":
                        print("🎯 SLOTS AVAILABLE - Check logs for details!")
                        slots = analysis.get("details", {}).get("slots", [])
                        for slot in slots:
                            self._log_detailed_slot_info(slot)
                        time.sleep(1800)  # 30 minutes
                    elif action == "join_waitlist":
                        print("📋 WAITLIST OPEN - Check logs for details!")
                        self._handle_available_slot("Netherlands", "Waitlist Open", waitlist=True)
                        time.sleep(1800)
                    elif action == "reauthenticate":
                        print("🔄 Session expired - Reauthenticating...")
                        self.retry_count += 1
                        if self.retry_count >= self.max_retries:
                            print("Max retries reached. Exiting...")
                            return
                        # Re-authenticate
                        self.auth_token = AuthHandler(
                            self.country, self.email, self.password, browser
                        ).authenticate()
                        if not self.auth_token:
                            print("Re-authentication failed. Exiting...")
                            return
                        time.sleep(30)
                    elif action == "wait_longer":
                        print("⏰ Rate limited - Waiting longer...")
                        time.sleep(300)  # 5 minutes
                    elif action == "retry":
                        print("🔄 Retrying...")
                        self.retry_count += 1
                        if self.retry_count >= self.max_retries:
                            print("Max retries reached. Exiting...")
                            return
                        time.sleep(60)
                    else:  # wait or continue
                        time.sleep(150)
                        
                except Exception as e:
                    print(f"Monitoring error: {e}")
                    self.log_appointment_data({
                        "type": "error",
                        "error": str(e)
                    })
                    self.retry_count += 1
                    if self.retry_count >= self.max_retries:
                        print("Max retries reached due to errors. Exiting...")
                        return
                    time.sleep(60)

    def _handle_comprehensive_response(self, response: Dict[str, Any], browser):
        """Handle the comprehensive response analysis"""
        analysis = response.get("analysis", {})
        status = analysis.get("status", "unknown")
        details = analysis.get("details", {})
        
        print(f"📊 Response Analysis: {status}")
        print(f"🎯 Action: {analysis.get('action', 'unknown')}")
        
        if status == "slots_available":
            slot_count = details.get("slot_count", 0)
            print(f"🎉 Found {slot_count} slot(s) available!")
            
        elif status == "waitlist_open":
            print("📋 Waitlist is open!")
            
        elif status == "session_expired":
            print("🔑 Session has expired")
            
        elif status == "rate_limited":
            print("⏱️ Rate limited by VFS")
            
        elif status == "no_slots":
            print("❌ No slots available")
            
        elif status == "api_error":
            error_info = details.get("error", {})
            print(f"⚠️ API Error: {error_info}")

    def _log_detailed_slot_info(self, slot: Dict[str, Any]):
        """Log detailed slot information"""
        try:
            print("=" * 60)
            print("🎯 DETAILED APPOINTMENT INFORMATION")
            print("=" * 60)
            print(f"📅 Appointment Type: {slot.get('appointment_type', 'Unknown')}")
            print(f"🏢 Center: {slot.get('center_name', 'Unknown')}")
            print(f"📋 Visa Category: {slot.get('visa_category', 'Unknown')}")
            print(f"📆 Earliest Date: {slot.get('earliest_date', 'Not specified')}")
            print(f"📍 Status: {slot.get('slot_status', 'Unknown')}")
            print(f"✅ Booking Allowed: {slot.get('booking_allowed', False)}")
            print(f"📝 Waitlist Available: {slot.get('waitlist_available', False)}")
            
            if slot.get('available_dates'):
                print(f"🗓️ Available Dates: {', '.join(slot['available_dates'][:5])}")
                
            if slot.get('time_slots'):
                print(f"⏰ Time Slots: {', '.join(slot['time_slots'][:3])}")
                
            # NLD specific information
            nld_info = slot.get('nld_specific', {})
            if nld_info:
                print("🇳🇱 NLD Specific Information:")
                if nld_info.get('document_type'):
                    print(f"   📄 Document Type: {nld_info['document_type']}")
                if nld_info.get('purpose_of_travel'):
                    print(f"   🎯 Purpose: {nld_info['purpose_of_travel']}")
                if nld_info.get('appointment_duration'):
                    print(f"   ⏱️ Duration: {nld_info['appointment_duration']}")
                if nld_info.get('required_documents'):
                    print(f"   📋 Required: {', '.join(nld_info['required_documents'])}")
            
            print("=" * 60)
            
            # Log to file
            self.log_appointment_data({
                "type": "detailed_slot",
                "slot_info": slot
            })
            
        except Exception as e:
            print(f"Error logging slot details: {e}")

    def _handle_available_slot(self, city=None, date_str=None, waitlist=False):
        if waitlist:
            self.notifier.send_sms(f"VFS Appointments waitlist Open")
        else:
            message = f"{self.country} {city} earliestDate {date_str}"
            self.notifier.send_sms(f"VFS Appointments {message} available")


if __name__ == "__main__":
    screen_keeper.start()

    accounts = [
        ("bgr", "umar.jwork@gmail.com", "P@ssword123"),
    ]

    print("VFS Scraper — Bulgaria (BGR)")
    print("=" * 50)
    print("Account : umar.jwork@gmail.com")
    print("Route   : GBR (London) -> BGR (Bulgaria)")
    print("=" * 50)

    for country, email, password in itertools.cycle(accounts):
        print(f"\n=== Starting session [{country.upper()}] {email} ===")
        scraper = VfsScraper(country, email, password)
        scraper.start_monitoring()
        print(f"=== Session ended. Restarting in 60s... ===")
        time.sleep(60)
