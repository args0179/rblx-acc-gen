import os
import time
import random
import string
import logging
import requests
from dataclasses import dataclass
from itertools import cycle
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ===================== LOGGING SETUP =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ===================== UI HELPERS =====================
def block(title):
    """Print a section header with separators"""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)

def line():
    """Print a simple separator line"""
    print("-" * 60)

# ===================== CONFIGURATION =====================
@dataclass
class Config:
    """Main configuration for the Roblox account generator"""
    
    # Browser settings
    chrome_binary: str = r""
    headless: bool = False
    
    # Account generation settings
    accounts_to_create: int = 20
    accounts_per_proxy: int = 3
    
    # Typing behavior
    human_typing: bool = False  # Enable/disable human-like typing
    typing_speed_min: float = 0.25  # Min delay between keystrokes (seconds)
    typing_speed_max: float = 0.35  # Max delay between keystrokes (seconds)
    
    # Captcha settings
    captcha_timeout_minutes: int = 10
    use_nopecha: bool = False
    nopecha_key: str = ""  # Get from nopecha.com
    
    # File paths
    accounts_file: str = r""
    cookies_file: str = r""
    proxy_file: str = r""
    adjectives_file: str = r""
    nouns_file: str = r""
    
    # Output settings
    save_cookies_separate: bool = True  # True = cookies in separate file, False = in accounts.txt
    
    # Proxy settings
    max_proxy_retries: int = 3
    proxy_test_timeout: int = 10

# ===================== TYPING SIMULATOR =====================
def type_text(element, text, config):
    """
    Type text into an element with optional human-like behavior
    
    Args:
        element: Selenium WebElement to type into
        text: String to type
        config: Config object containing typing settings
    """
    if config.human_typing:
        # Human-like typing with random delays
        for char in text:
            element.send_keys(char)
            delay = random.uniform(config.typing_speed_min, config.typing_speed_max)
            time.sleep(delay)
    else:
        # Fast typing without delays
        element.send_keys(text)

# ===================== DATA GENERATORS =====================
class PasswordGenerator:
    """Generate random secure passwords"""
    
    @staticmethod
    def generate(length=12):
        """
        Generate a random password
        
        Args:
            length: Password length (default 12)
            
        Returns:
            Random password string
        """
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(random.choice(chars) for _ in range(length))

class UsernameGenerator:
    """Generate random usernames from word lists"""
    
    def __init__(self, adjectives_file, nouns_file):
        """
        Initialize username generator with word lists
        
        Args:
            adjectives_file: Path to adjectives text file (one word per line)
            nouns_file: Path to nouns text file (one word per line)
        """
        self.adjectives = self._load_words(adjectives_file)
        self.nouns = self._load_words(nouns_file)
        
        # Fallback wordlists if files don't exist
        if not self.adjectives:
            logging.warning(f"Adjectives file not found: {adjectives_file}, using defaults")
            self.adjectives = ["Cool", "Swift", "Dark", "Bright", "Epic", "Ultra", 
                             "Mega", "Super", "Hyper", "Cyber", "ashy","Diff"]
        
        if not self.nouns:
            logging.warning(f"Nouns file not found: {nouns_file}, using defaults")
            self.nouns = ["Dragon", "Tiger", "Wolf", "Eagle", "Phoenix", "Ninja",
                         "Warrior", "Legend", "Ghost", "Shadow", "people", "world"]
    
    def _load_words(self, filepath):
        """
        Load words from a text file
        
        Args:
            filepath: Path to text file with one word per line
            
        Returns:
            List of words, or empty list if file not found
        """
        if not os.path.exists(filepath):
            return []
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                words = [line.strip() for line in f if line.strip()]
            logging.info(f"Loaded {len(words)} words from {filepath}")
            return words
        except Exception as e:
            logging.error(f"Failed to load {filepath}: {e}")
            return []
    
    def generate(self):
        """
        Generate a random username in format: AdjectiveNoun1234
        
        Returns:
            Username string (max 20 characters for Roblox)
        """
        adj = random.choice(self.adjectives)
        noun = random.choice(self.nouns)
        number = random.randint(1000, 9999)
        
        username = f"{adj}{noun}{number}"
        return username[:20]  # Roblox username limit

# ===================== PROXY MANAGER =====================
class ProxyManager:
    """Handle proxy rotation and testing"""
    
    def __init__(self, proxy_file, config):
        """
        Initialize proxy manager
        
        Args:
            proxy_file: Path to proxies.txt file
            config: Config object
        """
        self.config = config
        self.proxies = []
        self.failed_proxies = set()
        
        # Load proxies from file
        if os.path.exists(proxy_file):
            with open(proxy_file, "r", encoding="utf-8") as f:
                raw_proxies = [p.strip() for p in f if p.strip()]
            
            # Normalize proxy formats
            for proxy in raw_proxies:
                normalized = self._normalize_proxy(proxy)
                if normalized:
                    self.proxies.append(normalized)
        
        if not self.proxies:
            logging.warning("No valid proxies found - running without proxy")
        else:
            logging.info(f"Loaded {len(self.proxies)} proxies")
        
        # Create circular iterator for proxy rotation
        self.pool = cycle(self.proxies) if self.proxies else None
    
    def _normalize_proxy(self, proxy):
        """
        Normalize proxy format to include protocol
        
        Supports formats:
        - ip:port → http://ip:port
        - http://ip:port
        - http://user:pass@ip:port
        - socks5://ip:port
        
        Args:
            proxy: Raw proxy string
            
        Returns:
            Normalized proxy string or None if invalid
        """
        proxy = proxy.strip()
        
        # Add http:// if no protocol specified
        if not proxy.startswith(("http://", "https://", "socks4://", "socks5://")):
            proxy = f"http://{proxy}"
        
        # Validate proxy format
        try:
            parsed = urlparse(proxy)
            if not parsed.hostname:
                logging.warning(f"Invalid proxy format: {proxy}")
                return None
            return proxy
        except Exception as e:
            logging.warning(f"Invalid proxy: {proxy} - {e}")
            return None
    
    def is_alive(self, proxy):
        """
        Test if proxy is working
        
        Tests against multiple endpoints to ensure reliability
        
        Args:
            proxy: Proxy string to test
            
        Returns:
            True if proxy works, False otherwise
        """
        test_urls = [
            "https://api.ipify.org?format=json",
            "https://www.roblox.com/",
        ]
        
        proxies = {"http": proxy, "https": proxy}
        
        for url in test_urls:
            try:
                response = requests.get(
                    url,
                    proxies=proxies,
                    timeout=self.config.proxy_test_timeout,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                
                if response.status_code == 200:
                    # Log IP for verification
                    if "ipify" in url:
                        ip_data = response.json()
                        logging.info(f"Proxy IP verified: {ip_data.get('ip', 'unknown')}")
                    return True
                    
            except requests.exceptions.ProxyError:
                logging.debug(f"Proxy connection failed: {proxy}")
                return False
            except requests.exceptions.Timeout:
                logging.debug(f"Proxy timeout: {proxy}")
                return False
            except Exception as e:
                logging.debug(f"Proxy test error: {e}")
                continue
        
        return False
    
    def get_working_proxy(self):
        """
        Get next working proxy with automatic retry
        
        Returns:
            Working proxy string or None if no proxies available
        """
        if not self.proxies:
            return None
        
        attempts = 0
        max_attempts = len(self.proxies) * 2  # Allow 2 full rotations
        
        while attempts < max_attempts:
            proxy = next(self.pool)
            attempts += 1
            
            # Skip previously failed proxies
            if proxy in self.failed_proxies:
                continue
            
            logging.info(f"Testing proxy {attempts}/{max_attempts}: {proxy}")
            
            if self.is_alive(proxy):
                logging.info(f"Using proxy: {proxy}")
                return proxy
            else:
                self.failed_proxies.add(proxy)
                logging.warning(f"Proxy dead, trying next...")
        
        logging.error("All proxies failed - running without proxy")
        return None
    
    def mark_failed(self, proxy):
        """
        Mark a proxy as failed (e.g., rate limited)
        
        Args:
            proxy: Proxy string to mark as failed
        """
        if proxy:
            self.failed_proxies.add(proxy)
            logging.warning(f"Marked proxy as failed: {proxy}")

# ===================== COOKIE UTILITY =====================
def get_roblosecurity(driver):
    """
    Extract .ROBLOSECURITY cookie from browser
    
    Args:
        driver: Selenium WebDriver instance
        
    Returns:
        Cookie value string or None if not found
    """
    try:
        for cookie in driver.get_cookies():
            if cookie.get("name") == ".ROBLOSECURITY":
                return cookie.get("value")
    except Exception as e:
        logging.debug(f"Failed to get cookie: {e}")
    return None

# ===================== ACCOUNT GENERATOR =====================
class RobloxAccountGenerator:
    """Main class for generating Roblox accounts"""
    
    def __init__(self, cfg: Config):
        """
        Initialize account generator
        
        Args:
            cfg: Config object with all settings
        """
        self.cfg = cfg
        self.proxy_mgr = ProxyManager(cfg.proxy_file, cfg)
        self.username_gen = UsernameGenerator(cfg.adjectives_file, cfg.nouns_file)
        self.driver = None
        self.current_proxy = None
    
    def setup_driver(self, proxy):
        """
        Setup Chrome/Chromium WebDriver with anti-detection
        
        Args:
            proxy: Proxy string or None for direct connection
        """
        options = webdriver.ChromeOptions()
        options.binary_location = self.cfg.chrome_binary
        
        if self.cfg.headless:
            options.add_argument("--headless=new")
        
        # Configure proxy if provided
        if proxy:
            options.add_argument(f"--proxy-server={proxy}")
            logging.info(f"Browser using proxy: {proxy}")
        else:
            logging.info("Browser using direct connection (no proxy)")
        
        # Nopecha extension for captcha solving
        if self.cfg.use_nopecha:
            ext_path = "nopecha_ext.crx"
            
            if not os.path.exists(ext_path):
                logging.info("Downloading Nopecha extension...")
                try:
                    r = requests.get("https://nopecha.com/f/ext.crx", timeout=30)
                    with open(ext_path, "wb") as f:
                        f.write(r.content)
                    logging.info("Nopecha extension downloaded")
                except Exception as e:
                    logging.error(f"Failed to download Nopecha: {e}")
                    self.cfg.use_nopecha = False
            
            if os.path.exists(ext_path):
                options.add_extension(ext_path)
                logging.info("Nopecha extension loaded")
        
        # Anti-detection measures
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        options.add_argument("log-level=3")
        
        self.driver = webdriver.Chrome(options=options)
        self.current_proxy = proxy
        
        # Remove webdriver property for stealth
        self.driver.execute_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        
        # Configure Nopecha API key if provided
        if self.cfg.use_nopecha and self.cfg.nopecha_key:
            try:
                logging.info("Setting Nopecha API key...")
                self.driver.get(f"https://nopecha.com/setup#{self.cfg.nopecha_key}")
                time.sleep(2)
                self.driver.get(f"https://nopecha.com/setup#{self.cfg.nopecha_key}")
                time.sleep(1)
                logging.info("Nopecha key configured")
            except Exception as e:
                logging.error(f"Failed to set Nopecha key: {e}")
    
    def signup(self, username, password):
        """
        Fill out and submit Roblox signup form
        
        Args:
            username: Username to register
            password: Password to use
            
        Raises:
            RuntimeError: If signup fails (validation errors, rate limit, etc.)
        """
        self.driver.get("https://www.roblox.com/")
        time.sleep(5)
        
        # Handle cookie consent banner (EU regions)
        try:
            cookie_btn = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, '//button[contains(@class, "cookie-btn")]'))
            )
            cookie_btn.click()
            logging.info("Closed cookie banner")
            time.sleep(1)
        except:
            logging.info("No cookie banner found")
        
        # Wait for signup form to load
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "MonthDropdown"))
            )
            logging.info("Signup form loaded")
        except Exception as e:
            logging.error(f"Signup form not loaded: {e}")
            raise
        
        # Fill birthday dropdowns (required for Roblox)
        for dropdown_id in ["MonthDropdown", "DayDropdown", "YearDropdown"]:
            try:
                dropdown = WebDriverWait(self.driver, 15).until(
                    EC.element_to_be_clickable((By.ID, dropdown_id))
                )
                dropdown.click()
                time.sleep(0.5)
                
                # Wait for dropdown options to populate
                WebDriverWait(self.driver, 10).until(
                    lambda drv: len(drv.find_elements(By.CSS_SELECTOR, f"#{dropdown_id} option")) > 1
                )
                
                options = self.driver.find_elements(By.CSS_SELECTOR, f"#{dropdown_id} option")
                
                # Special handling for year (ensure valid age range)
                if dropdown_id == "YearDropdown":
                    valid_years = []
                    for opt in options[1:]:  # Skip first empty option
                        try:
                            year = int(opt.text.strip())
                            if 1978 <= year <= 2010:  # Ages 15-47 as of 2025
                                valid_years.append(opt)
                        except:
                            pass
                    
                    if valid_years:
                        selected = random.choice(valid_years)
                        selected.click()
                        logging.info(f"Selected year: {selected.text}")
                    else:
                        random.choice(options[1:]).click()
                else:
                    random.choice(options[1:]).click()
                
                time.sleep(0.5)
                logging.info(f"Selected {dropdown_id}")
            except Exception as e:
                logging.error(f"Failed to select {dropdown_id}: {e}")
                raise
        
        # Fill username and password fields
        user_el = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "signup-username"))
        )
        pass_el = self.driver.find_element(By.ID, "signup-password")
        
        type_text(user_el, username, self.cfg)
        type_text(pass_el, password, self.cfg)
        
        # Check for validation errors (username taken, invalid, etc.)
        time.sleep(1)
        try:
            error_el = self.driver.find_element(By.ID, "signup-usernameInputValidation")
            error_text = error_el.text.strip()
            if error_text:
                logging.error(f"Username validation error: {error_text}")
                raise RuntimeError(f"Username error: {error_text}")
        except RuntimeError:
            raise
        except:
            pass  # No error found
        
        # Accept Terms of Use checkbox (if present)
        try:
            terms_checkbox = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.ID, "signup-checkbox"))
            )
            if not terms_checkbox.is_selected():
                terms_checkbox.click()
                logging.info("Accepted Terms of Use")
                time.sleep(0.5)
        except:
            logging.info("No Terms checkbox found")
        
        # Submit signup form
        signup_btn = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.ID, "signup-button"))
        )
        signup_btn.click()
        logging.info("Clicked signup button")
        
        # Check for post-submission errors
        time.sleep(3)
        try:
            general_error = self.driver.find_element(By.ID, "GeneralErrorText")
            error_text = general_error.text.strip()
            if error_text:
                logging.error(f"Signup error: {error_text}")
                
                # Handle rate limiting
                if "too many" in error_text.lower() or "rate" in error_text.lower():
                    logging.error("RATE LIMITED")
                    if self.current_proxy:
                        self.proxy_mgr.mark_failed(self.current_proxy)
                    raise RuntimeError("Rate limited")
                elif "unknown error" in error_text.lower():
                    logging.error("Unknown error from Roblox")
                    raise RuntimeError("Unknown error")
                else:
                    raise RuntimeError(f"Signup error: {error_text}")
        except RuntimeError:
            raise
        except:
            pass  # No error found
    
    def wait_captcha(self):
        """
        Wait for captcha to be solved (by Nopecha or manually)
        
        Returns:
            True if account created successfully, False if timeout/error
        """
        start = time.time()
        timeout = self.cfg.captcha_timeout_minutes * 60
        
        if self.cfg.use_nopecha:
            logging.info(f"Nopecha will auto-solve captcha (max {self.cfg.captcha_timeout_minutes} minutes)")
        else:
            logging.info(f"Waiting for MANUAL captcha completion (max {self.cfg.captcha_timeout_minutes} minutes)")
        
        # Wait for redirect to /home (indicates success)
        while time.time() - start < timeout:
            current_url = self.driver.current_url
            
            if "roblox.com/home" in current_url:
                logging.info("Redirected to /home - Account created!")
                return True
            
            # Check for errors during captcha
            try:
                error = self.driver.find_element(By.ID, "GeneralErrorText")
                if error.text.strip():
                    logging.error(f"Error during captcha: {error.text}")
                    return False
            except:
                pass
            
            # Show progress every 10 seconds
            elapsed = int(time.time() - start)
            if elapsed % 10 == 0 and elapsed > 0:
                remaining = int(timeout - elapsed)
                logging.info(f"Still waiting... ({remaining}s remaining)")
            
            time.sleep(2)
        
        logging.error("Captcha timeout")
        return False
    
    def save_account(self, username, password, cookie):
        """
        Save account credentials to file(s)
        
        Args:
            username: Account username
            password: Account password
            cookie: .ROBLOSECURITY cookie value or None
        """
        # Save username and password
        with open(self.cfg.accounts_file, "a", encoding="utf-8") as f:
            if self.cfg.save_cookies_separate:
                # Save only username:password
                f.write(f"{username}:{password}\n")
            else:
                # Save username:password:cookie
                cookie_str = cookie if cookie else "NO_COOKIE"
                f.write(f"{username}:{password}:{cookie_str}\n")
        
        # Save cookie to separate file if configured
        if self.cfg.save_cookies_separate and cookie:
            with open(self.cfg.cookies_file, "a", encoding="utf-8") as f:
                f.write(f"{cookie}\n")
        
        if cookie:
            logging.info("Saved with cookie")
        else:
            logging.warning("Saved without cookie")
    
    def run(self):
        """Main execution loop for account generation"""
        block("ROBLOX ACCOUNT GENERATOR STARTED")
        
        # Show configuration
        if self.cfg.use_nopecha:
            if self.cfg.nopecha_key:
                logging.info("CAPTCHA MODE: Nopecha Auto-Solve (with API key)")
            else:
                logging.warning("Nopecha enabled but NO API KEY! Get one at: https://nopecha.com")
                logging.info("CAPTCHA MODE: Nopecha Auto-Solve (FREE tier - limited)")
        else:
            logging.info("CAPTCHA MODE: Manual (you must solve)")
        
        if self.cfg.human_typing:
            logging.info("TYPING MODE: Human-like (slow)")
        else:
            logging.info("TYPING MODE: Fast (instant)")
        
        if self.cfg.save_cookies_separate:
            logging.info(f"OUTPUT: Accounts in {self.cfg.accounts_file}, Cookies in {self.cfg.cookies_file}")
        else:
            logging.info(f"OUTPUT: Everything in {self.cfg.accounts_file}")
        
        used = 0
        proxy = None
        success_count = 0
        fail_count = 0
        
        for i in range(1, self.cfg.accounts_to_create + 1):
            # Rotate proxy after N accounts
            if used % self.cfg.accounts_per_proxy == 0:
                proxy = self.proxy_mgr.get_working_proxy()
                block(f"PROXY → {proxy if proxy else 'NONE (Direct connection)'}")
            
            used += 1
            username = self.username_gen.generate()
            password = PasswordGenerator.generate()
            
            block(f"ACCOUNT #{i} / {self.cfg.accounts_to_create}")
            print(f"Username : {username}")
            print(f"Password : {password}")
            line()
            
            retry_count = 0
            max_retries = self.cfg.max_proxy_retries
            
            while retry_count < max_retries:
                try:
                    self.setup_driver(proxy)
                    self.signup(username, password)
                    
                    if not self.wait_captcha():
                        raise RuntimeError("Captcha timeout or error")
                    
                    cookie = get_roblosecurity(self.driver)
                    self.save_account(username, password, cookie)
                    
                    block("ACCOUNT CREATED SUCCESS")
                    success_count += 1
                    
                    if self.driver:
                        self.driver.quit()
                    
                    break  # Success, exit retry loop
                
                except Exception as e:
                    retry_count += 1
                    block("ERROR")
                    
                    logging.error(f"Exception type: {type(e).__name__}")
                    logging.error(f"Exception message: {str(e)}")
                    
                    error_msg = str(e).lower()
                    
                    # Handle rate limiting with proxy rotation
                    if "rate limit" in error_msg or "too many" in error_msg:
                        logging.error("Rate limited! Getting new proxy...")
                        if self.driver:
                            try:
                                self.driver.quit()
                            except:
                                pass
                        
                        # Get new proxy
                        proxy = self.proxy_mgr.get_working_proxy()
                        
                        if retry_count < max_retries:
                            logging.info(f"Retry {retry_count}/{max_retries} with new proxy...")
                            continue
                    
                    # Suggest fixes for other errors
                    if "timeout" in error_msg:
                        logging.error("Suggestion: Check internet connection or increase timeout")
                    elif "captcha" in error_msg:
                        logging.error("Suggestion: Check Nopecha settings")
                    elif "username" in error_msg:
                        logging.error("Suggestion: Username issue, will retry with new username")
                    
                    if self.driver:
                        try:
                            self.driver.quit()
                        except:
                            pass
                    
                    if retry_count >= max_retries:
                        fail_count += 1
                        logging.error(f"Failed after {max_retries} retries")
                        break
                    
                    time.sleep(5)
        
        block("GENERATION COMPLETE")
        print(f"Success: {success_count}")
        print(f"Failed:  {fail_count}")
        print(f"Total:   {self.cfg.accounts_to_create}")
        print(f"Saved to: {self.cfg.accounts_file}")
        if self.cfg.save_cookies_separate:
            print(f"Cookies:  {self.cfg.cookies_file}")

# ===================== ENTRY POINT =====================
if __name__ == "__main__":
    RobloxAccountGenerator(Config()).run()