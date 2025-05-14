#!/usr/bin/env python3
import requests
import subprocess
import os
import random
import string
import time
import threading
import json
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
import queue
from urllib.parse import unquote
import telegram_bot

# Configuration
OUTPUT_FILE = "valid.txt"
CHECKPOINT_FILE = "checkpoint.json"
LOGS_FILE = "logs.txt"
# System name for identifying this instance
SYSTEM_NAME = ""
LADDOO_PATTERNS = [
    "iplladdoo2025",
    "socialTitle=Psst",  
    "Laddoo+for+you"     
]

LADDOO_TYPES = [
    "Steady",
    "Sparky",
    "Zen",
    "Elastic",
    "Boom",
    "Dash",
    "Bazooka",
    "Dizzy",
    "Sunny",
    "Ninja",
    "Wally"
]
# Telegram Bot Configuration
ENABLE_TELEGRAM = True  # Set to False to disable Telegram notifications
TELEGRAM_BOT_TOKEN = "<telegram_bot_token_here>"  # Bot token
TELEGRAM_CHAT_ID = "<telegram_chat_id_here>"  # Chat ID
# Checkpoint Configuration
CHECKPOINT_SEND = True  # Set to False to disable checkpoint sharing via Telegram
CHECKPOINT_SHARE_INTERVAL = 3600  # How often to share checkpoint 
CHECKPOINT_AUTO_SAVE_INTERVAL = 600  # How often to auto-save checkpoint 
MAX_WORKERS = min(30, os.cpu_count() * 4)  # Adjust based on system capabilities
BATCH_SIZE = 100  # Write results in batches
REQUEST_TIMEOUT = 8  # Timeout for requests
VERBOSE = True  # Always log verbose output to logs.txt

# Global variables for checkpoint system
processed_codes = set()
checkpoint_lock = threading.Lock()
checkpoint_sender_stop_event = threading.Event()
checkpoint_sender_thread = None
checkpoint_auto_save_stop_event = threading.Event()
checkpoint_auto_save_thread = None
last_auto_save_time = 0


# Logger function to write logs to file instead of printing to terminal
def log_message(message, level="INFO"):
    """Write log message to logs file with timestamp"""
    if not VERBOSE:
        return
    
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_file_name = f"{SYSTEM_NAME}-{LOGS_FILE}" if SYSTEM_NAME else LOGS_FILE
    with open(log_file_name, "a") as log_file:
        log_file.write(f"[{timestamp}] [{level}] {message}\n")

# Thread-safe counter
class Counter:
    def __init__(self, initial=0):
        self.count = initial
        self.lock = threading.Lock()
    
    def increment(self):
        with self.lock:
            self.count += 1
            return self.count
    
    def value(self):
        with self.lock:
            return self.count

# Thread-safe set for tracking processed URLs
class UrlTracker:
    def __init__(self):
        self.processed_urls = set()
        self.lock = threading.Lock()
    
    def is_processed(self, url):
        with self.lock:
            return url in self.processed_urls
    
    def mark_processed(self, url):
        with self.lock:
            if url in self.processed_urls:
                return False
            self.processed_urls.add(url)
            return True
    
    def get_processed_count(self):
        with self.lock:
            return len(self.processed_urls)

# Thread-safe queue for results
class ResultQueue:
    def __init__(self):
        self.queue = queue.Queue()
    
    def put(self, item):
        self.queue.put(item)
    
    def get(self, timeout=None):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

# Result writer for batch processing
class ResultWriter(threading.Thread):
    def __init__(self, result_queue, url_tracker, output_file):
        threading.Thread.__init__(self)
        self.daemon = True
        self.result_queue = result_queue
        self.output_file = output_file
        self.url_tracker = url_tracker
        self.running = True
        self.batch = []
        self.file_lock = threading.Lock()
    
    def run(self):
        while self.running:
            try:
                item = self.result_queue.get(timeout=2)
                if item is None:
                    self._write_batch()
                    break
                
                if self.url_tracker.mark_processed(item):
                    self.batch.append(item)
                    self._write_batch()
            except:
                if self.batch:
                    self._write_batch()
    
    def _write_batch(self):
        if self.batch:
            with self.file_lock:
                with open(self.output_file, "a") as f:
                    for url in self.batch:
                        f.write(f"{url}\n")
            self.batch = []
    
    def stop(self):
        self.running = False
        self.result_queue.put(None)

# Function to check if a URL is valid using curl
def extract_laddoo_type(url):
    """Extract the laddoo type from a URL if present"""
    for laddoo_type in LADDOO_TYPES:
        if laddoo_type.lower() in url.lower():
            return laddoo_type
    return "Other"  # If no specific type is found

def is_valid_url(code, use_curl=True):
    """First check if URL is valid (returns HTTP 200 or redirects) before analyzing patterns"""
    url = f"https://gpay.app.goo.gl/{code}"
    try:
        if use_curl:
            # Use curl to check response code
            check_result = subprocess.run(
                ["curl", "-o", "/dev/null", "-s", "-w", "%{http_code}", url],
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT
            )
            status_code = int(check_result.stdout.strip())
            
            log_message(f"URL validity check for {url} returned status code: {status_code}", "DEBUG")
            
            return status_code >= 200 and status_code < 400  # Valid if 2XX or 3XX
        else:
            # Use requests to check response code
            response = requests.head(url, allow_redirects=False, timeout=REQUEST_TIMEOUT)
            status_code = response.status_code
            
            log_message(f"URL validity check for {url} returned status code: {status_code}", "DEBUG")
            
            return status_code >= 200 and status_code < 400  # Valid if 2XX or 3XX
    except Exception as e:
        log_message(f"Error checking URL validity for {url}: {str(e)}", "ERROR")
        return False

def get_logs_filename():
    """Return the current logs filename including system name if set"""
    return f"{SYSTEM_NAME}-{LOGS_FILE}" if SYSTEM_NAME else LOGS_FILE

def check_url_curl(code):
    url = f"https://gpay.app.goo.gl/{code}"
    try:
        # First check if URL is valid before doing pattern check
        if not is_valid_url(code, use_curl=True):
            log_message(f"Skipping pattern check for invalid URL: {url}", "INFO")
            return (False, code, None, None, None)
        
        log_message(f"Running curl check for {url}", "INFO")
        
        # Get redirect URL
        result = subprocess.run(
            ["curl", "-Ls", "-o", "/dev/null", "-w", "%{url_effective}", url],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT
        )
        
        final_url = result.stdout.strip()
        # Decode URL-encoded characters for better pattern matching
        decoded_url = unquote(final_url)
        
        # Log URL analysis to file
        log_message("URL analysis:", "DEBUG")
        log_message(f"Original URL: {url}", "DEBUG")
        log_message(f"Final URL: {final_url}", "DEBUG")
        log_message(f"Decoded URL: {decoded_url}", "DEBUG")
        
        # Check that ALL patterns are found (either in raw or decoded URL)
        all_patterns_found = True
        found_patterns = []
        missing_patterns = []
        
        # Log pattern detection
        log_message(f"Checking for ALL patterns: {LADDOO_PATTERNS}", "DEBUG")
        
        for pattern in LADDOO_PATTERNS:
            in_raw = pattern in final_url
            in_decoded = pattern in decoded_url
            
            # Check if pattern is found in either raw or decoded URL
            if in_raw or in_decoded:
                found_patterns.append(pattern)
                if in_raw:
                    log_message(f"Found pattern in raw URL: {pattern}", "DEBUG")
                if in_decoded:
                    log_message(f"Found pattern in decoded URL: {pattern}", "DEBUG")
            else:
                all_patterns_found = False
                missing_patterns.append(pattern)
                log_message(f"Missing pattern: {pattern}", "DEBUG")
        
        if all_patterns_found:
            log_message(f"All {len(LADDOO_PATTERNS)} required patterns found!", "SUCCESS")
            log_message(f"VALID Laddoo URL found: {url}", "SUCCESS")
            log_message(f"All patterns found: {found_patterns}", "INFO")
            
            print("\n" + "=" * 60)
            print(f"✅ VALID Laddoo URL FOUND: {url}")
            print(f"🔗 Redirect URL: {final_url}")
            
            # Extract laddoo type from the URL
            laddoo_type = extract_laddoo_type(decoded_url)
            log_message(f"Identified laddoo type: {laddoo_type}", "INFO")
            
            # Log results
            log_message(f"URL {url} is valid with patterns: {found_patterns}", "INFO")
            log_message(f"Redirects to: {final_url}", "INFO")
            log_message(f"Laddoo Type: {laddoo_type}", "INFO")
            
            # Write to results file
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"{url} -> {final_url} (Laddoo Type: {laddoo_type})\n")
            
            # Immediately send to Telegram if enabled
            if ENABLE_TELEGRAM:
                try:
                    success = telegram_bot.send_valid_link(code, final_url, found_patterns, SYSTEM_NAME, laddoo_type)
                    if success:
                        print("✓ Notification sent to Telegram")
                    else:
                        print("✗ Failed to send Telegram notification")
                except Exception as e:
                    log_message(f"Error sending Telegram notification: {e}", "ERROR")
            
            return (True, code, url, final_url, found_patterns, laddoo_type)
        else:
            log_message(f"Found only {len(found_patterns)}/{len(LADDOO_PATTERNS)} required patterns", "INFO")
            log_message(f"Missing patterns: {missing_patterns}", "DEBUG")
            return (False, code, None, final_url, None)
    except Exception as e:
        log_message(f"Error in curl check: {str(e)}", "ERROR")
        return (False, code, f"Error: {str(e)}", None, None)

# Function to check if a URL is valid using requests
def check_url_requests(code):
    url = f"https://gpay.app.goo.gl/{code}"
    try:
        # First check if URL is valid before doing pattern check
        if not is_valid_url(code, use_curl=False):
            log_message(f"Skipping pattern check for invalid URL: {url}", "INFO")
            return (False, code, None, None, None)
            
        log_message(f"Running requests check for {url}", "INFO")
        
        # Use requests library to get and follow redirects
        response = requests.get(url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        status_code = response.status_code
        final_url = response.url
        
        # Decode URL-encoded characters for better pattern matching
        decoded_url = unquote(str(final_url))
        
        # Log URL analysis details to file
        log_message("URL analysis:", "DEBUG")
        log_message(f"Original URL: {url}", "DEBUG")
        log_message(f"Status Code: {status_code}", "DEBUG")
        log_message(f"Final URL: {final_url}", "DEBUG")
        log_message(f"Decoded URL: {decoded_url}", "DEBUG")
        
        # Check that ALL patterns are found (either in raw or decoded URL)
        all_patterns_found = True
        found_patterns = []
        missing_patterns = []
        
        # Log pattern detection
        log_message(f"Checking for ALL patterns: {LADDOO_PATTERNS}", "DEBUG")
        
        for pattern in LADDOO_PATTERNS:
            in_raw = pattern in final_url
            in_decoded = pattern in decoded_url
            
            # Check if pattern is found in either raw or decoded URL
            if in_raw or in_decoded:
                found_patterns.append(pattern)
                if in_raw:
                    log_message(f"Found pattern in raw URL: {pattern}", "DEBUG")
                if in_decoded:
                    log_message(f"Found pattern in decoded URL: {pattern}", "DEBUG")
            else:
                all_patterns_found = False
                missing_patterns.append(pattern)
                log_message(f"Missing pattern: {pattern}", "DEBUG")
        
        if all_patterns_found:
            log_message(f"All {len(LADDOO_PATTERNS)} required patterns found!", "SUCCESS")
            log_message(f"VALID Laddoo URL found: {url}", "SUCCESS")
            log_message(f"All patterns found: {found_patterns}", "INFO")
            
            if all_patterns_found or len(found_patterns) > 0:
                # Extract laddoo type from the URL
                laddoo_type = extract_laddoo_type(decoded_url)
                log_message(f"Identified laddoo type: {laddoo_type}", "INFO")
                
                print("\n" + "=" * 60)
                print(f"✅ VALID Laddoo URL FOUND: {url}")
                print(f"🔗 Redirect URL: {final_url}")
                print(f"🍮 Laddoo Type: {laddoo_type}")
                print("Found patterns:")
                for pattern in found_patterns:
                    print(f"  ✓ {pattern}")
                print("=" * 60)
                

                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"{url} -> {final_url} (Laddoo Type: {laddoo_type})\n")
                print(f"✓ Saved to {OUTPUT_FILE}")
                
                # Immediately send to Telegram if enabled
                if ENABLE_TELEGRAM:
                    try:
                        success = telegram_bot.send_valid_link(code, final_url, found_patterns, SYSTEM_NAME, laddoo_type)
                        if success:
                            print("✓ Notification sent to Telegram")
                        else:
                            print("⚠️ Failed to send Telegram notification")
                    except Exception as e:
                        log_message(f"Error sending Telegram notification: {str(e)}", "ERROR")
                        print(f"⚠️ Error sending Telegram notification: {str(e)}")
                
                return (True, code, url, final_url, found_patterns, laddoo_type)  # Success with details
        else:
            log_message(f"Found only {len(found_patterns)}/{len(LADDOO_PATTERNS)} required patterns", "INFO")
            log_message(f"Missing patterns: {missing_patterns}", "DEBUG")
            return (False, code, None, final_url, None)
    except Exception as e:
        log_message(f"Error in requests check: {str(e)}", "ERROR")
        return (False, code, f"Error: {str(e)}", None, None)

# ... (rest of the code remains the same)

def worker(work_queue, result_queue, counter, url_tracker, use_curl=False, stop_event=None):
    """Worker function for threads"""
    check_func = check_url_curl if use_curl else check_url_requests
    
    while not stop_event.is_set():
        try:
            code = work_queue.get(timeout=1)
            if code is None or stop_event.is_set():
                break
                
            count = counter.increment()
            if count % 100 == 0:
                log_message(f"Progress: {count} codes processed", "INFO")
                print(f"[PROGRESS] Checked {count} codes so far...")
                
            valid, code, redirect_url, found_patterns, missing_patterns = check_func(code)
            
            # Valid links are handled directly in the check_url functions now
            # This function just needs to add the result to the queue
            if valid:
                result_queue.put(f"https://gpay.app.goo.gl/{code}")
            work_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            log_message(f"Worker error: {str(e)}", "ERROR")

# Generate random code with specified length
def generate_random_code(length=6):
    """Generate a random alphanumeric code with specified length."""
    charset = string.ascii_letters + string.digits
    return ''.join(random.choice(charset) for _ in range(length))

# Save checkpoint
def checkpoint_sender_task():
    """Thread task that periodically sends checkpoint data to Telegram"""
    global checkpoint_sender_stop_event
    
    log_message("Checkpoint sender thread started", "INFO")
    
    while not checkpoint_sender_stop_event.is_set():
        try:
            # Send checkpoint file if it exists and CHECKPOINT_SEND is enabled
            if CHECKPOINT_SEND and ENABLE_TELEGRAM and os.path.exists(CHECKPOINT_FILE):
                telegram_bot.send_checkpoint(CHECKPOINT_FILE)
                log_message("Checkpoint shared via Telegram", "INFO")
            
            # Wait for the specified interval or until stopped
            checkpoint_sender_stop_event.wait(CHECKPOINT_SHARE_INTERVAL)
        except Exception as e:
            log_message(f"Error in checkpoint sender thread: {str(e)}", "ERROR")
            # Wait a bit before retrying
            checkpoint_sender_stop_event.wait(60)

def start_checkpoint_sender():
    """Start the checkpoint sender thread if not already running"""
    global checkpoint_sender_thread, checkpoint_sender_stop_event
    
    # Only start if Telegram and checkpoint sharing are enabled
    if not (ENABLE_TELEGRAM and CHECKPOINT_SEND):
        log_message("Checkpoint sharing via Telegram is disabled", "INFO")
        return False
    
    if checkpoint_sender_thread and checkpoint_sender_thread.is_alive():
        log_message("Checkpoint sender thread already running", "INFO")
        return True
    
    checkpoint_sender_stop_event.clear()
    checkpoint_sender_thread = threading.Thread(target=checkpoint_sender_task)
    checkpoint_sender_thread.daemon = True
    checkpoint_sender_thread.start()
    log_message("Started checkpoint sender thread", "INFO")
    return True

def stop_checkpoint_sender():
    """Stop the checkpoint sender thread"""
    global checkpoint_sender_thread, checkpoint_sender_stop_event
    
    if checkpoint_sender_thread and checkpoint_sender_thread.is_alive():
        checkpoint_sender_stop_event.set()
        checkpoint_sender_thread.join(timeout=5)
        log_message("Stopped checkpoint sender thread", "INFO")

def checkpoint_auto_save_task():
    """Thread task that periodically saves checkpoint data"""
    global checkpoint_auto_save_stop_event, processed_codes, counter, last_auto_save_time
    
    log_message("Checkpoint auto-save thread started", "INFO")
    
    while not checkpoint_auto_save_stop_event.is_set():
        try:
            current_time = time.time()
            # Only save if there are processed codes and time interval has passed
            if processed_codes and current_time - last_auto_save_time >= CHECKPOINT_AUTO_SAVE_INTERVAL:
                save_checkpoint(processed_codes, counter.value())
                last_auto_save_time = current_time
                log_message(f"Auto-saved checkpoint with {counter.value()} codes", "INFO")
            
            # Wait for the specified interval or until stopped
            # Use shorter intervals to be more responsive to stop events
            for _ in range(10):  # Check every INTERVAL/10 seconds
                if checkpoint_auto_save_stop_event.is_set():
                    break
                checkpoint_auto_save_stop_event.wait(CHECKPOINT_AUTO_SAVE_INTERVAL / 10)
        except Exception as e:
            log_message(f"Error in checkpoint auto-save thread: {str(e)}", "ERROR")
            checkpoint_auto_save_stop_event.wait(60)

def start_checkpoint_auto_save():
    """Start the checkpoint auto-save thread if not already running"""
    global checkpoint_auto_save_thread, checkpoint_auto_save_stop_event, last_auto_save_time
    
    if checkpoint_auto_save_thread and checkpoint_auto_save_thread.is_alive():
        log_message("Checkpoint auto-save thread already running", "INFO")
        return True
    
    checkpoint_auto_save_stop_event.clear()
    last_auto_save_time = time.time()  
    checkpoint_auto_save_thread = threading.Thread(target=checkpoint_auto_save_task)
    checkpoint_auto_save_thread.daemon = True
    checkpoint_auto_save_thread.start()
    log_message(f"Started checkpoint auto-save thread (interval: {CHECKPOINT_AUTO_SAVE_INTERVAL} seconds)", "INFO")
    return True

def stop_checkpoint_auto_save():
    """Stop the checkpoint auto-save thread"""
    global checkpoint_auto_save_thread, checkpoint_auto_save_stop_event
    
    if checkpoint_auto_save_thread and checkpoint_auto_save_thread.is_alive():
        checkpoint_auto_save_stop_event.set()
        checkpoint_auto_save_thread.join(timeout=5)
        log_message("Stopped checkpoint auto-save thread", "INFO")

def save_checkpoint(processed, counter_value):
    """Save checkpoint to file"""
    try:
        # Create checkpoint data
        checkpoint_data = {
            "codes": list(processed),
            "counter": counter_value,
            "timestamp": time.time()
        }
        
        with checkpoint_lock:
            # Write to temp file first
            temp_file = f"{CHECKPOINT_FILE}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(checkpoint_data, f)
            
            # Rename temp file to actual checkpoint file (atomic operation)
            if os.path.exists(CHECKPOINT_FILE):
                os.replace(temp_file, CHECKPOINT_FILE)
            else:
                os.rename(temp_file, CHECKPOINT_FILE)
        
        log_message(f"Checkpoint saved with {counter_value} codes", "INFO")
        return True
    except Exception as e:
        log_message(f"Failed to save checkpoint: {str(e)}", "ERROR")
        return False

# Load checkpoint
def load_checkpoint():
    """Load checkpoint data from file"""
    try:
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, "r") as f:
                checkpoint_data = json.load(f)
            
            # Handle both old and new format checkpoints
            processed_codes = set(checkpoint_data.get("codes", []) or checkpoint_data.get("processed_codes", []))
            processed_count = checkpoint_data.get("counter", 0) or checkpoint_data.get("processed_count", 0)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(checkpoint_data.get("timestamp", time.time()))) or checkpoint_data.get("date_readable", "unknown")
            
            # Log detailed info to file
            log_message(f"Checkpoint loaded: {processed_count} codes from {timestamp}", "INFO")
            print(f"\n[*] Loaded checkpoint: {processed_count} codes processed")
            return processed_codes, processed_count
        else:
            return set(), 0
    except Exception as e:
        log_message(f"Error loading checkpoint: {str(e)}", "ERROR")
        print("\n[!] Error loading checkpoint")
        return set(), 0

# Signal handler for graceful exit
def signal_handler(sig, frame):
    global processed_codes, counter
    print("\n[!] Keyboard interrupt detected, saving checkpoint before exit...")
    # Save checkpoint before exiting
    try:
        save_checkpoint(processed_codes, counter.value())
        print(f"[*] Checkpoint saved: {counter.value()} codes")
        log_message(f"Checkpoint saved by signal handler: {counter.value()} codes", "INFO")
        
        # Stop the checkpoint auto-save thread if running
        try:
            stop_checkpoint_auto_save()
        except Exception as e:
            log_message(f"Error stopping checkpoint auto-save: {str(e)}", "ERROR")
        
        # Stop the checkpoint sender thread if running
        try:
            stop_checkpoint_sender()
        except Exception as e:
            log_message(f"Error stopping checkpoint sender: {str(e)}", "ERROR")
        
        # Stop Telegram log sender if enabled
        if ENABLE_TELEGRAM:
            try:
                telegram_bot.stop_log_sender()
                log_message("Stopped Telegram log sender", "INFO")
            except Exception as e:
                log_message(f"Error stopping Telegram log sender: {str(e)}", "ERROR")
    except Exception as e:
        log_message(f"Error saving checkpoint in signal handler: {str(e)}", "ERROR")
        print(f"[!] Error saving checkpoint: {str(e)}")
    
    sys.exit(0)

# Function to test single code
def test_single_code(code, use_curl=False):
    """Simple test function that checks a single code and logs details to file instead of printing"""
    print(f"Testing URL: https://gpay.app.goo.gl/{code}")
    print("")
    
    if use_curl:
        result = check_url_curl(code)
    else:
        result = check_url_requests(code)
    
    if len(result) >= 6:  
        valid, code, url, final_url, patterns_found, laddoo_type = result
    else:  
        valid, code, url, final_url, patterns_found = result
        laddoo_type = "Unknown"
    
    if not valid:
        print(f"\n❌ URL is not valid: https://gpay.app.goo.gl/{code}")
        log_message(f"Tested URL: https://gpay.app.goo.gl/{code} - Not valid", "INFO")
    else:
        if "🍮 Laddoo Type" not in str(patterns_found):
            print(f"\n🍮 Laddoo Type: {laddoo_type}")
    
    return valid

# Main function
def ask_for_system_name():
    """Ask the user to name the system for identification"""
    global SYSTEM_NAME, LOGS_FILE

    
    system_name = input("\nEnter a name for this Process (For Identification The System): ")
    
    if system_name and system_name.strip():
        SYSTEM_NAME = system_name.strip()
        print(f"\nSystem Named as: {SYSTEM_NAME}")
        log_message(f"System Named as: {SYSTEM_NAME}", "INFO")
    else:
        print("\nNo name provided, using default settings.")
        SYSTEM_NAME = ""
    
    # Initialize Telegram bot with system name
    if ENABLE_TELEGRAM:
        telegram_bot.initialize_bot(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SYSTEM_NAME)

def main():
    """Main function for Laddoos Finder Tool"""
    global processed_codes, counter
    
    try:
        # Clear screen
        os.system('cls' if os.name=='nt' else 'clear')
        
        # Print banner
        banner = '''
--  ___      _______  ______   ______   _______  _______ 
-- |   |    |   _   ||      | |      | |       ||       |
-- |   |    |  |_|  ||  _    ||  _    ||   _   ||   _   |
-- |   |    |       || | |   || | |   ||  | |  ||  | |  |
-- |   |___ |       || |_|   || |_|   ||  |_|  ||  |_|  |
-- |       ||   _   ||       ||       ||       ||       |
-- |_______||__| |__||______| |______| |_______||_______|
--  __   __  __   __  __    _  _______                   
-- |  | |  ||  | |  ||  |  | ||       |                  
-- |  |_|  ||  | |  ||   |_| ||_     _|                  
-- |       ||  |_|  ||       |  |   |                    
-- |       ||       ||  _    |  |   |                    
-- |   _   ||       || | |   |  |   |                    
-- |__| |__||_______||_|  |__|  |___|                                                 
'''

        print(banner.center(60))
        print("=" * 60)
        print("🔍 Hunting Ladoooos".center(60))
        print("=" * 60)
        
        ask_for_system_name()
        
        log_file_name = get_logs_filename()
        if not os.path.exists(log_file_name):
            open(log_file_name, 'w').close()
        log_message("Starting Laddoo Hunting", "INFO")
        
        # Start the background sender for logs if Telegram is enabled
        if ENABLE_TELEGRAM:
            try:
                telegram_bot.start_log_sender()
                log_message("Telegram integration initialized successfully", "INFO")
                
                # Start checkpoint sender if configured
                if CHECKPOINT_SEND:
                    start_checkpoint_sender()
                    log_message(f"Checkpoint sharing enabled (interval: {CHECKPOINT_SHARE_INTERVAL} seconds)", "INFO")
                else:
                    log_message("Checkpoint sharing is disabled", "INFO")
            except Exception as e:
                log_message(f"Error initializing Telegram integration: {str(e)}", "ERROR")
                log_message("Continuing without Telegram integration", "WARNING")
        
        # Create initial logs file with header
        log_message("Laddoo Finder Started", "INFO")
        
        # Load checkpoint if exists
        processed_codes, last_count = load_checkpoint()
        counter = Counter(len(processed_codes))
        log_message(f"Checkpoint loaded: {len(processed_codes)} codes processed", "INFO")
        
        # Start automatic checkpoint saving (runs in background)
        start_checkpoint_auto_save()
        log_message(f"Automatic checkpoint saving enabled (every {CHECKPOINT_AUTO_SAVE_INTERVAL} seconds)", "INFO")
        
        use_curl = False
        if os.name != 'nt' or subprocess.run(["curl", "--version"], shell=True, capture_output=True).returncode == 0:
            use_curl_input = input("\n Use curl instead of requests? (Faster, Recommended) (y/n): ")
            use_curl = use_curl_input.lower() == 'y'
            print(f"\n[*] Using {'curl' if use_curl else 'requests'} for URL checks")
            log_message(f"Using {'curl' if use_curl else 'requests'} for URL checks", "INFO")
        
        resume = False
        if len(processed_codes) > 0:
            resume_choice = input(f"\nFound checkpoint with {len(processed_codes)} codes. Resume from checkpoint? (y/n): ")
            resume = resume_choice.lower() == 'y'
            if not resume:
                processed_codes = set()
                counter = Counter(0)
                print("\n[*] Starting fresh session")
                log_message("Starting fresh session, ignoring checkpoint", "INFO")
            else:
                print(f"\n[*] Resuming from checkpoint with {len(processed_codes)} processed codes")
                log_message(f"Resuming from checkpoint with {len(processed_codes)} processed codes", "INFO")
        
        # Set up work queues and threads
        work_queue = queue.Queue(maxsize=100000)
        result_queue = ResultQueue()
        url_tracker = UrlTracker()
        stop_event = threading.Event()
        
        # Start worker threads
        workers = []
        for _ in range(MAX_WORKERS):
            t = threading.Thread(
                target=worker,
                args=(work_queue, result_queue, counter, url_tracker, use_curl, stop_event)
            )
            t.daemon = True
            t.start()
            workers.append(t)
            
        # Start bruteforce
        try:
            # Install signal handlers
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            test_single = input("\nTest a specific code first? (y/n): ").lower() == 'y'
            if test_single:
                test_code = input("Enter code to test (e.g - 6Cf87y,UMtJXP): ").strip()
                if test_code:
                    test_single_code(test_code, use_curl)
            
            input("\nPress Enter to start bruteforce...")
            
            os.system('cls' if os.name=='nt' else 'clear')
            
            # Start bruteforce
            print(banner.center(60))
            print("=" * 60)
            print(f"\n[*] Starting bruteforce with {MAX_WORKERS} threads...")
            print(f"[*] Press Ctrl+C at any time to pause and save checkpoint")
            
            # Auto-save checkpoint periodically
            last_checkpoint_time = time.time()
            checkpoint_interval = 300  
            
            # Generate and queue random codes
            while True:
                code = generate_random_code(6)
                if code in processed_codes:
                    continue
                    
                work_queue.put(code)
                processed_codes.add(code)
                
                # Periodic checkpoint saving
                current_time = time.time()
                if current_time - last_checkpoint_time > checkpoint_interval:
                    save_checkpoint(processed_codes, counter.value())
                    last_checkpoint_time = current_time
                    print(f"[*] Auto-saved checkpoint: {counter.value()} codes")
                
                # Prevent queue from growing too large
                if work_queue.qsize() > 10000:
                    time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n[!] Keyboard interrupt detected, saving checkpoint before exit...")
            stop_event.set()
        finally:
            save_checkpoint(processed_codes, counter.value())
            for t in workers:
                t.join(timeout=1)
            if ENABLE_TELEGRAM:
                try:
                    telegram_bot.stop_log_sender()
                    log_message("Stopped Telegram log sender", "INFO")
                except Exception as e:
                    log_message(f"Error stopping Telegram log sender: {str(e)}", "ERROR")
    except Exception as e:
        log_message(f"Error: {str(e)}", "ERROR")
        if ENABLE_TELEGRAM:
            try:
                telegram_bot.stop_log_sender()
                log_message("Stopped Telegram log sender", "INFO")
            except Exception as e:
                log_message(f"Error stopping Telegram log sender: {str(e)}", "ERROR")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # This should be handled by the signal_handler
        # but just in case it's not, handle it here too
        print("\n[!] Program interrupted by user")
        try:
            save_checkpoint(processed_codes, counter.value())
            print(f"[*] Checkpoint saved: {counter.value()} codes")
            log_message("Program interrupted by user", "INFO")
            log_message(f"Checkpoint saved: {counter.value()} codes", "INFO")
        except Exception as e:
            print(f"[!] Error saving checkpoint: {str(e)}")
            
        if ENABLE_TELEGRAM:
            try:
                telegram_bot.stop_log_sender()
                log_message("Stopped Telegram log sender", "INFO")
            except Exception as e:
                log_message(f"Error stopping Telegram log sender: {str(e)}", "ERROR")
        sys.exit(0)
    except Exception as e:
        print(f"\n[!] Unexpected error: {str(e)}")
        try:
            save_checkpoint(processed_codes, counter.value())
            print("[*] Checkpoint saved despite error")
            log_message(f"Checkpoint saved despite error: {str(e)}", "ERROR")
        except Exception as save_error:
            print(f"[!] Could not save checkpoint: {str(save_error)}")
            
        if ENABLE_TELEGRAM:
            try:
                telegram_bot.stop_log_sender()
                log_message("Stopped Telegram log sender", "INFO")
            except Exception as e:
                log_message(f"Error stopping Telegram log sender: {str(e)}", "ERROR")