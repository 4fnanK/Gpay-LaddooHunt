#!/usr/bin/env python3
import os
import time
import threading
import logging
import requests
import json

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a file handler for the logger to keep logs in a file
log_handler = logging.FileHandler('telegram_bot.log')
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(log_handler)

# Prevent logs from propagating to the root logger (which outputs to console)
logger.propagate = False

# Configuration
LOGS_FILE = "logs.txt"
LOG_SEND_INTERVAL = 3600  # How often to share checkpoint (in seconds, default 1 hour)
SYSTEM_NAME = ""  # Will be set during initialization

class TelegramNotifier:
    """
    Handles sending notifications to a Telegram bot.
    This class manages sending valid links and periodic log updates.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern to ensure only one instance exists"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TelegramNotifier, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self, token=None, chat_id=None, system_name=None):
        """Initialize the TelegramNotifier with bot token and chat ID"""
        if self._initialized:
            return
            
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.system_name = system_name or ""
        
        if not self.token or not self.chat_id:
            logger.warning("Telegram bot token or chat ID not provided. Notifications will be disabled.")
            self.enabled = False
        else:
            self.base_url = f"https://api.telegram.org/bot{self.token}"
            self.enabled = True
            
        self.log_sender_thread = None
        self._stop_event = threading.Event()
        self._initialized = True
    
    def start_log_sender(self):
        """Start the periodic log sender thread"""
        if not self.enabled:
            logger.warning("Telegram notifications disabled. Not starting log sender.")
            return
            
        if self.log_sender_thread and self.log_sender_thread.is_alive():
            logger.info("Log sender thread already running")
            return
            
        self._stop_event.clear()
        self.log_sender_thread = threading.Thread(target=self._log_sender_task)
        self.log_sender_thread.daemon = True
        self.log_sender_thread.start()
        logger.info("Started log sender thread")
    
    def stop_log_sender(self):
        """Stop the periodic log sender thread"""
        if self.log_sender_thread and self.log_sender_thread.is_alive():
            self._stop_event.set()
            self.log_sender_thread.join(timeout=5)
            logger.info("Stopped log sender thread")
    
    def _log_sender_task(self):
        """Task that sends logs periodically"""
        while not self._stop_event.is_set():
            try:
                self.send_logs()
            except Exception as e:
                logger.error(f"Error sending logs: {str(e)}")
            
            # Sleep for the interval (or until stopped)
            self._stop_event.wait(LOG_SEND_INTERVAL)
    
    def send_valid_link(self, code, redirect_url=None, patterns_found=None, system_name=None, laddoo_type=None):
        """Send notification about a valid link"""
        if not self.enabled:
            return False
            
        try:
            url = f"https://gpay.app.goo.gl/{code}"
            active_system_name = system_name or self.system_name
            
            message = f"🎯 *Valid Ladoo Found!*\n\n"
            if active_system_name:
                message += f"• System: *{active_system_name}*\n"
            message += f"• Code: `{code}`\n"
            message += f"• URL: {url}\n"
            
            if laddoo_type:
                message += f"• Ladoo Type: *{laddoo_type}*\n"
            
            if redirect_url:
                message += f"• Redirects to: {redirect_url}\n"
                
            if patterns_found:
                message += "\n*Patterns Found:*\n"
                for pattern in patterns_found:
                    message += f"✅ {pattern}\n"
            
            # Use requests library to send message via Telegram API
            api_url = f"{self.base_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }
            
            response = requests.post(api_url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Sent valid link notification for code: {code}")
            return True
        except Exception as e:
            logger.error(f"Failed to send valid link notification: {str(e)}")
            return False
    
    def get_logs_filename(self):
        """Get the correct logs filename with system name if available"""
        if self.system_name:
            return f"{self.system_name}-{LOGS_FILE}"
        return LOGS_FILE
            
    def send_logs(self):
        """Send the logs.txt file to the configured chat"""
        if not self.enabled:
            return False
            
        logs_file = self.get_logs_filename()
        
        if not os.path.exists(logs_file):
            logger.warning(f"Logs file {logs_file} not found")
            return False
            
        try:

            if os.path.getsize(logs_file) == 0:
                logger.info("Logs file is empty, not sending")
                return False
                

            api_url = f"{self.base_url}/sendDocument"
            
            # Add system name to caption if available
            if self.system_name:
                caption = f"📜 Ladoo Finder Logs ({self.system_name}) - {time.strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                caption = f"📜 Ladoo Finder Logs - {time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            with open(logs_file, 'rb') as log_file:
                files = {'document': log_file}
                data = {'chat_id': self.chat_id, 'caption': caption}
                
                response = requests.post(api_url, data=data, files=files, timeout=30)
                response.raise_for_status()
            
            # After successfully sending logs, delete the logs file completely
            try:

                import glob

                backup_pattern = f"{self.system_name}-logs_backup_*.txt" if self.system_name else "logs_backup_*.txt"
                for backup_file in glob.glob(backup_pattern):
                    try:
                        os.remove(backup_file)
                        logger.info(f"Deleted old backup file: {backup_file}")
                    except Exception as backup_error:
                        logger.error(f"Failed to delete backup file {backup_file}: {str(backup_error)}")
                

                with open(logs_file, 'w') as log_file:
                    log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Logs cleared after sending to Telegram\n")
                
                logger.info("Logs file cleared after sending to Telegram")
            except Exception as e:
                logger.error(f"Failed to clear logs after sending: {str(e)}")
                
            logger.info("Sent logs file to Telegram")
            return True
        except Exception as e:
            logger.error(f"Failed to send logs: {str(e)}")
            return False
    
    def send_checkpoint(self, checkpoint_file):
        """Send checkpoint file to Telegram"""
        if not self.enabled:
            return False
            
        if not os.path.exists(checkpoint_file):
            logger.warning(f"Checkpoint file {checkpoint_file} not found")
            return False
            
        try:
            # Check if file exists and has content
            if os.path.getsize(checkpoint_file) == 0:
                logger.info("Checkpoint file is empty, not sending")
                return False
                

            api_url = f"{self.base_url}/sendDocument"
            

            if self.system_name:
                caption = f"📊 Checkpoint Data ({self.system_name}) - {time.strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                caption = f"📊 Checkpoint Data - {time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            # Read the checkpoint file to get progress information
            try:
                with open(checkpoint_file, 'r') as f:
                    checkpoint_data = json.load(f)
                    total_codes = checkpoint_data.get('counter', 0)
                    caption += f"\nTotal Codes Processed: {total_codes}"
            except Exception as json_error:
                logger.error(f"Error reading checkpoint data: {str(json_error)}")
            
            with open(checkpoint_file, 'rb') as checkpoint_file_obj:
                files = {'document': checkpoint_file_obj}
                data = {'chat_id': self.chat_id, 'caption': caption}
                
                response = requests.post(api_url, data=data, files=files, timeout=30)
                response.raise_for_status()
            
            logger.info("Sent checkpoint file to Telegram")
            return True
        except Exception as e:
            logger.error(f"Failed to send checkpoint: {str(e)}")
            return False

    def test_notification(self):
        """Send a test notification to verify the bot is working"""
        if not self.enabled:
            return False
            
        try:
            message = (
                "🧪 *Test Notification*\n\n"
                "The Ladoo Finder Telegram bot is configured correctly "
                "and is ready to send notifications."
            )
            
            # Use requests to send message
            api_url = f"{self.base_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }
            
            response = requests.post(api_url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info("Sent test notification")
            return True
        except Exception as e:
            logger.error(f"Failed to send test notification: {str(e)}")
            return False

# Helper functions for external use
def initialize_bot(token, chat_id, system_name=None):
    """Initialize the Telegram bot with the given token and chat ID and optional system name"""
    global SYSTEM_NAME
    SYSTEM_NAME = system_name or ""
    notifier = TelegramNotifier(token, chat_id, system_name)
    return notifier

def send_valid_link(code, redirect_url=None, patterns_found=None, system_name=None, laddoo_type=None):
    """Send notification about a valid link"""
    notifier = TelegramNotifier()
    return notifier.send_valid_link(code, redirect_url, patterns_found, system_name, laddoo_type)

def start_log_sender():
    """Start the periodic log sender thread"""
    notifier = TelegramNotifier()
    notifier.start_log_sender()

def stop_log_sender():
    """Stop the periodic log sender thread"""
    notifier = TelegramNotifier()
    notifier.stop_log_sender()

def test_notification():
    """Send a test notification to verify the bot is working"""
    notifier = TelegramNotifier()
    return notifier.test_notification()

def send_checkpoint(checkpoint_file):
    """Send checkpoint file to Telegram"""
    notifier = TelegramNotifier()
    return notifier.send_checkpoint(checkpoint_file)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Telegram Bot for Laddoo Finder")
    parser.add_argument("--token", help="Telegram Bot Token")
    parser.add_argument("--chat-id", help="Telegram Chat ID")
    parser.add_argument("--test", action="store_true", help="Send a test notification")
    parser.add_argument("--send-logs", action="store_true", help="Send logs file")
    
    args = parser.parse_args()
    
    if args.token and args.chat_id:
        notifier = initialize_bot(args.token, args.chat_id)
        
        if args.test:
            notifier.test_notification()
        
        if args.send_logs:
            notifier.send_logs()
    else:
        if args.test or args.send_logs:
            print("Error: Both --token and --chat-id are required")
        else:
            print("Run with --help for usage information")
