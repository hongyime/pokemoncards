import os
import json
import csv
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple
import sys

# --- Configuration ---
API_BASE_URL = "https://api.pokemontcg.io/v2"
SETS_ENDPOINT = f"{API_BASE_URL}/sets"
SETS_CACHE_FILE = "sets_cache.json"
DOWNLOADED_SETS_CSV = "downloaded_sets.csv"
DOWNLOADED_CARDS_CSV = "downloaded_cards.csv"
MAX_RETRIES = 0 # Can be 0 for no retries
MAX_WORKERS = 10 # Number of parallel download threads

# --- CSV Headers Definition (Ensuring backward compatibility) ---
SETS_HEADERS = [
    'set_id', 'set_name', 'printed_total', 'last_updated', 'last_checked',
    'cards_downloaded', 'set_complete'
]
CARDS_HEADERS = [
    'set_id', 'set_name', 'card_number', 'filename', 'download_date',
    'image_url', 'is_hires', 'file_size', 'download_duration', 'status'
]

class PokemonCardDownloader:
    """
    A professional, resilient, and parallelized utility for downloading Pokémon card images
    and tracking collection progress.
    """
    def __init__(self):
        """Initializes the downloader and establishes the initial state."""
        self.image_dir: Optional[str] = None
        self.downloaded_sets_data: Dict[str, Dict[str, Any]] = self._load_csv_data(DOWNLOADED_SETS_CSV, 'set_id')
        self.downloaded_cards_data: Dict[Tuple[str, str], Dict[str, Any]] = self._load_csv_data(DOWNLOADED_CARDS_CSV, ('set_id', 'card_number'))
        self.api_sets_data: Dict[str, Dict[str, Any]] = {}
        self.stop_script = False

    def _load_csv_data(self, filename: str, key_field: Any) -> Dict:
        """Loads data from a CSV file into a dictionary for quick lookup."""
        data = {}
        if not os.path.exists(filename):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: {filename} not found. Will be created upon processing.")
            return data

        try:
            with open(filename, mode='r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                # Ensure headers match the expected keys for backward compatibility
                expected_keys = set(SETS_HEADERS) if filename == DOWNLOADED_SETS_CSV else set(CARDS_HEADERS)
                
                for row in reader:
                    # Filter out any unexpected columns to maintain compatibility
                    row = {k: v for k, v in row.items() if k in expected_keys}
                    
                    if isinstance(key_field, str):
                        key = row[key_field]
                    elif isinstance(key_field, tuple):
                        # Use tuple key for set_id and card_number
                        key = tuple(row.get(field) for field in key_field) 
                    data[key] = row
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Loaded {len(data)} records from {filename}.")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: Failed to load {filename}. Data might be corrupted. {e}")
        return data

    def _save_csv_data(self, filename: str, headers: List[str], data: Dict):
        """Writes the current dictionary data back to a CSV file (atomic write for resilience)."""
        if not data:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] WARNING: Skipping save for {filename}. Data dictionary is empty.")
            return

        temp_filename = filename + '.tmp'
        try:
            with open(temp_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                for row in data.values():
                    # Ensure only defined headers are written
                    filtered_row = {k: row.get(k, '') for k in headers}
                    writer.writerow(filtered_row)
            
            # Atomic file replacement for resilience
            os.replace(temp_filename, filename)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: Progress saved to {filename}.")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL: Failed to save progress to {filename}. {e}")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            self.stop_script = True

    # --- API and Cache Management ---

    def _fetch_all_sets_from_api(self) -> Optional[List[Dict[str, Any]]]:
        """Fetches all sets from the API and updates the local cache."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Requesting all set data from API...")
        try:
            response = requests.get(SETS_ENDPOINT, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            sets = data.get('data', [])
            
            # Save the raw response to JSON cache file
            with open(SETS_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: Downloaded {len(sets)} sets and saved to {SETS_CACHE_FILE}.")
            return sets
        except requests.exceptions.RequestException as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL: API request failed. Check network or API key (if applicable). {e}")
            return None
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL: Failed to save JSON cache. {e}")
            return None

    def _load_or_fetch_sets(self, force_update: bool = False) -> bool:
        """Loads sets from cache or fetches from API if needed/forced."""
        sets = None
        if not force_update and os.path.exists(SETS_CACHE_FILE):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Loading sets from local cache: {SETS_CACHE_FILE}")
            try:
                with open(SETS_CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    sets = data.get('data', [])
                print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: Loaded {len(sets)} sets from cache.")
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] WARNING: Failed to read {SETS_CACHE_FILE}. Forcing API fetch. {e}")
                sets = self._fetch_all_sets_from_api()
        else:
            sets = self._fetch_all_sets_from_api()

        if sets is None:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] FATAL: Could not retrieve set data from cache or API.")
            return False

        # Transform list of sets into a dictionary for quick lookup
        self.api_sets_data = {s['id']: s for s in sets}
        return True

    # --- CSV Population Logic ---

    def _populate_sets_csv(self) -> None:
        """Updates the sets CSV with all available sets from the JSON data."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Updating {DOWNLOADED_SETS_CSV}...")
        new_entries = 0
        updated_entries = 0
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for set_id, api_set in self.api_sets_data.items():
            printed_total_str = str(api_set.get('printedTotal', api_set.get('total', 0)))

            if set_id not in self.downloaded_sets_data:
                # New set found: create a new entry
                new_row = {
                    'set_id': set_id,
                    'set_name': api_set['name'],
                    'printed_total': printed_total_str,
                    'last_updated': api_set.get('updatedAt', 'N/A'),
                    'last_checked': current_time_str,
                    'cards_downloaded': 0,
                    'set_complete': 'False' # Default status
                }
                self.downloaded_sets_data[set_id] = new_row
                new_entries += 1
            else:
                # Existing set: update relevant metadata fields
                existing_row = self.downloaded_sets_data[set_id]
                metadata = {
                    'set_name': api_set['name'],
                    'printed_total': printed_total_str,
                    'last_updated': api_set.get('updatedAt', existing_row['last_updated']),
                    'last_checked': current_time_str,
                }
                if any(existing_row.get(key) != value for key, value in metadata.items()):
                    existing_row.update(metadata)
                    updated_entries += 1

        if new_entries > 0 or updated_entries > 0 or not os.path.exists(DOWNLOADED_SETS_CSV):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Added {new_entries} sets; updated metadata for {updated_entries} existing sets.")
            self._save_csv_data(DOWNLOADED_SETS_CSV, SETS_HEADERS, self.downloaded_sets_data)
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: No new sets found or metadata changes.")

    def _populate_cards_csv(self):
        """Fills the cards CSV with all possible card entries derived from the sets data."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Updating {DOWNLOADED_CARDS_CSV} with all card entries...")
        new_card_entries = 0
        
        # Iterate over all sets tracked in the SETS CSV (which were synchronized from JSON)
        for set_id, set_row in self.downloaded_sets_data.items():
            try:
                printed_total = int(set_row['printed_total'])
            except ValueError:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] WARNING: Invalid printed_total for set {set_id}. Skipping card population.")
                continue

            set_name = set_row['set_name']

            for i in range(1, printed_total + 1):
                card_number_str = str(i)
                card_key = (set_id, card_number_str)

                if card_key not in self.downloaded_cards_data:
                    # New card entry: populate with defaults for download
                    
                    # Construct image URL based on established pattern (set_id/card_number_hires.png)
                    # This calculation is vital for a robust manifest
                    image_url = f"https://images.pokemontcg.io/{set_id}/{card_number_str}_hires.png"
                    filename = f"{card_number_str}_hires.png"

                    new_row = {
                        'set_id': set_id,
                        'set_name': set_name,
                        'card_number': card_number_str,
                        'filename': filename,
                        'download_date': 'N/A',
                        'image_url': image_url,
                        'is_hires': 'True',
                        'file_size': 0,
                        'download_duration': 0.0,
                        'status': 'pending' # Default status
                    }
                    self.downloaded_cards_data[card_key] = new_row
                    new_card_entries += 1
        
        if new_card_entries > 0 or not os.path.exists(DOWNLOADED_CARDS_CSV):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Appended {new_card_entries} new card entries to {DOWNLOADED_CARDS_CSV}.")
            self._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, self.downloaded_cards_data)
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: All potential card entries are already tracked in {DOWNLOADED_CARDS_CSV}.")

    # --- Core Download Logic (Parallelized) ---

    def _get_save_directory(self) -> Optional[str]:
        """
        Prompts user for image save location ONCE and sets self.image_dir.
        Returns the directory or None on failure.
        """
        if self.image_dir is None:
            while True:
                input_dir = input("Enter the root directory to save images: ")
                if not input_dir:
                    print("Directory cannot be empty.")
                    continue
                
                try:
                    # Use absolute path for clarity and stability
                    abs_dir = os.path.abspath(input_dir)
                    os.makedirs(abs_dir, exist_ok=True)
                    self.image_dir = abs_dir
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Saving images to: {self.image_dir}")
                    return self.image_dir
                except Exception as e:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: Failed to create directory: {e}. Please try again.")
        return self.image_dir

    def _download_card(self, card_key: Tuple[str, str], card_data: Dict[str, Any], root_dir: str) -> bool:
        """
        Attempts to download a single card image with retries.
        Returns True on success, False on failure.
        """
        set_id, card_number = card_key
        set_name = card_data['set_name']
        url = card_data['image_url']
        local_filename = card_data['filename']
        
        # Determine the save path: ROOT/set_name (set_id)/filename
        save_folder = os.path.join(root_dir, f"{set_name}")
        os.makedirs(save_folder, exist_ok=True) # Ensure set-specific subfolder exists
        save_path = os.path.join(save_folder, local_filename)

        if os.path.exists(save_path) and card_data.get('status') == 'success':
            # Skip already successful and existing files
            return True

        retries = 0
        while retries < MAX_RETRIES + 1: # Loop MAX_RETRIES times for retries (and 1 for initial attempt)
            if retries > 0:
                 print(f"[{datetime.now().strftime('%H:%M:%S')}] WARNING: {set_id}/{card_number} failed. Retrying in 2s (Attempt {retries}/{MAX_RETRIES})...")
                 time.sleep(2) # Implement a brief delay before retrying
            
            try:
                start_time = time.time()
                response = requests.get(url, stream=True, timeout=30)
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

                with open(save_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                
                # Success: Update card data
                end_time = time.time()
                card_data['status'] = 'success'
                card_data['download_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                card_data['file_size'] = os.path.getsize(save_path)
                card_data['download_duration'] = round(end_time - start_time, 4)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: {set_id}/{card_number} downloaded in {card_data['download_duration']}s.")
                return True

            except requests.exceptions.RequestException as e:
                # Network or HTTP error
                pass # Let the loop continue to retry

            except Exception as e:
                # Catch file I/O errors or other unforeseen exceptions
                card_data['status'] = f'failed (Unknown Error)'
                print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL: Unhandled error for {set_id}/{card_number}. {e}")
                return False

            retries += 1 # Increment retry counter

        # Failure after max retries
        card_data['status'] = f'failed (Max Retries {MAX_RETRIES})'
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR: {set_id}/{card_number} failed after {MAX_RETRIES + 1} attempts. URL: {url}.")
        return False # Should only be reached if all retries fail


    def _process_downloads(self):
        """Manages the parallel download process for all 'pending' cards."""
        
        # 1. Identify pending tasks (retry failed cards in the new run)
        cards_to_download = [
            (k, v) for k, v in self.downloaded_cards_data.items() 
            if v['status'] in ('pending', 'N/A') or 'failed' in v['status']
        ]

        if not cards_to_download:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: No pending card downloads found. Collection up-to-date.")
            return

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] START: Initiating parallel download for {len(cards_to_download)} cards with {MAX_WORKERS} workers.")
        
        failed_cards_in_run: Dict[Tuple[str, str], int] = {}
        successful_downloads_count = 0
        
        # Use the guaranteed root directory
        root_dir = self.image_dir

        # 2. Execute parallel downloads
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_card = {
                executor.submit(self._download_card, card_key, card_data, root_dir): card_key
                for card_key, card_data in cards_to_download
            }
            
            for future in as_completed(future_to_card):
                if self.stop_script:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL: Script terminated by critical error. Shutting down executor.")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                card_key = future_to_card[future]
                card_success = future.result()
                
                # Check for critical failure logic
                if not card_success and f'Max Retries {MAX_RETRIES}' in self.downloaded_cards_data[card_key]['status']:
                    set_id, card_number = card_key
                    failed_cards_in_run[card_key] = failed_cards_in_run.get(card_key, 0) + 1
                    
                    if len(failed_cards_in_run) >= 100000000000000000000000: # Check unique card failures
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] FATAL: 3 or more unique cards ({list(failed_cards_in_run.keys())}) failed their maximum retries. Killing script and saving progress.")
                        self.stop_script = True
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                elif card_success:
                    successful_downloads_count += 1
                
                # Save progress after a batch (e.g., every 50 successful downloads)
                if successful_downloads_count % 50 == 0 and successful_downloads_count > 0:
                     self._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, self.downloaded_cards_data)
        
        # 3. Final save of cards CSV
        self._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, self.downloaded_cards_data)
        
        # 4. Update Sets CSV (Checking for completeness)
        self._update_sets_completion()


    def _update_sets_completion(self):
        """Checks if all cards in a set are 'success' and updates the set_complete status."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Checking set completion status...")
        set_counts: Dict[str, Tuple[int, int]] = {} # (downloaded_success_count, total_count)
        updated_sets = 0

        # 1. Tally successful downloads per set
        for card_data in self.downloaded_cards_data.values():
            set_id = card_data['set_id']
            if set_id not in set_counts:
                # Use the printed_total from the sets CSV for the current total
                total = int(self.downloaded_sets_data.get(set_id, {}).get('printed_total', 0))
                set_counts[set_id] = [0, total]
            
            if card_data.get('status') == 'success':
                set_counts[set_id][0] += 1
        
        # 2. Update set records
        for set_id, (downloaded, total) in set_counts.items():
            if set_id in self.downloaded_sets_data:
                set_row = self.downloaded_sets_data[set_id]
                
                if total > 0:
                    is_complete = downloaded >= total
                else:
                    is_complete = False

                # Update cards_downloaded count
                if int(set_row['cards_downloaded']) != downloaded:
                    set_row['cards_downloaded'] = downloaded
                    updated_sets += 1

                # Update completion status
                current_status = set_row['set_complete']
                if current_status == 'False' and is_complete:
                    set_row['set_complete'] = 'True'
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: Set '{set_id}' is now marked as complete (Downloaded: {downloaded}/{total}).")
                    updated_sets += 1
                elif current_status == 'True' and not is_complete:
                    set_row['set_complete'] = 'False'
                    updated_sets += 1

        if updated_sets > 0:
            self._save_csv_data(DOWNLOADED_SETS_CSV, SETS_HEADERS, self.downloaded_sets_data)
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: No changes to set completion status.")

    # --- Main Execution Functions ---

    def run_full_download(self, force_api_fetch: bool = False):
        """
        Executes the main download workflow.
        """
        if not self._load_or_fetch_sets(force_update=force_api_fetch):
            return

        self._populate_sets_csv()
        if self.stop_script: return

        self._populate_cards_csv()
        if self.stop_script: return
        
        # --- CRITICAL FIX: Get Directory ONCE before parallel threads start ---
        if self._get_save_directory() is None:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] FATAL: Image directory setup failed. Aborting download.")
            return
        # ---------------------------------------------------------------------
        
        self._process_downloads()
        
        if not self.stop_script:
            print("\n========================================================")
            print("All required download and synchronization tasks are complete.")
            print("========================================================\n")

    def show_stats(self):
        """
        Summarizes collection statistics, using the latest API/JSON data 
        as the baseline for total available sets and cards.
        """
        # STEP 1: Ensure JSON data is the BASELINE (Option 3 requirement)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Retrieving latest set data (API/Cache) for baseline comparison.")
        if not self._load_or_fetch_sets(force_update=False):
            print("WARNING: Using local CSV data for set totals due to critical failure to load API/Cache baseline.")
        
        # Use API/JSON data for the definitive total count
        total_sets_baseline = len(self.api_sets_data)
        total_cards_baseline = sum(
            int(set_data.get('printedTotal', set_data.get('total', 0))) 
            for set_data in self.api_sets_data.values()
        )

        # STEP 2: Compare with CSV data for progress tracking
        # Reload the latest download progress from the cards CSV
        self.downloaded_cards_data = self._load_csv_data(DOWNLOADED_CARDS_CSV, ('set_id', 'card_number'))
        
        # Calculate downloaded progress
        successful_cards = sum(1 for c in self.downloaded_cards_data.values() if c.get('status') == 'success')
        failed_cards = sum(1 for c in self.downloaded_cards_data.values() if 'failed' in str(c.get('status', '')))
        
        # Calculate sets complete based on the official baseline
        complete_sets = 0
        for set_id, api_set in self.api_sets_data.items():
            baseline_total = int(api_set.get('printedTotal', api_set.get('total', 0)))
            downloaded_count = sum(1 for (s_id, c_num), c_data in self.downloaded_cards_data.items() 
                                   if s_id == set_id and c_data.get('status') == 'success')
            
            if baseline_total > 0 and downloaded_count >= baseline_total:
                complete_sets += 1
        
        # Final calculations
        download_progress_pct = (successful_cards / total_cards_baseline) * 100 if total_cards_baseline > 0 else 0.0
        
        # Calculate total size (must reload sets data for accurate total size if not just running Option 1)
        total_size_bytes = sum(int(c.get('file_size', 0)) for c in self.downloaded_cards_data.values())
        total_size_mb = total_size_bytes / (1024 * 1024)

        print("\n========================================================")
        print("          COLLECTION DOWNLOAD STATISTICS")
        print("========================================================")
        
        # Set Stats
        print("📊 Set Progress (Based on latest API data):")
        print(f"  - Total Available Sets: {total_sets_baseline}")
        print(f"  - Complete Sets:      {complete_sets}")
        
        # Card Stats
        print("\n🖼️ Card Download Status:")
        print(f"  - Total Cards Available (Baseline): {total_cards_baseline}")
        print(f"  - Successfully Downloaded: {successful_cards}")
        print(f"  - Download Progress:  {download_progress_pct:.2f}%")
        print(f"  - Failed (Needs Review): {failed_cards}")

        # Storage Stats
        print("\n💾 Storage Summary:")
        print(f"  - Total Disk Space Used: {total_size_mb:.2f} MB")
        print("========================================================")

# --- Main Application Execution ---

def main():
    """Provides the command-line interface for the user and handles graceful shutdown."""
    downloader = PokemonCardDownloader()
    
    try:
        while True:
            print("\n--------------------------------------------------------")
            print("        Pokémon Card Downloader Utility")
            print("--------------------------------------------------------")
            print("1. Start/Resume Download (Use cache if available)")
            print("2. Update Set List and Resume Download (Force API fetch)")
            print("3. Show Download Statistics")
            print("4. Exit")
            print("--------------------------------------------------------")

            choice = input("Select an option (1-4): ")

            try:
                if choice == '1':
                    downloader.run_full_download(force_api_fetch=False)
                elif choice == '2':
                    downloader.run_full_download(force_api_fetch=True)
                elif choice == '3':
                    downloader.show_stats()
                elif choice == '4':
                    print("Exiting utility. Goodbye! 👋")
                    break
                else:
                    print("Invalid choice. Please select 1, 2, 3, or 4.")
                
                if downloader.stop_script:
                    print("The utility terminated prematurely due to a critical error or repeated card failure.")
                    break

            except Exception as e:
                # Catch general script errors during execution
                print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL ERROR: An unexpected error occurred: {e}", file=sys.stderr)
                # Ensure a critical error also tries to save before exiting
                if not downloader.stop_script:
                    downloader._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, downloader.downloaded_cards_data)
                    downloader._update_sets_completion()
                break

    except KeyboardInterrupt:
        # **CRITICAL FIX**: Handle Ctrl+C for graceful exit and progress save
        print("\n\n[Ctrl+C Detected] WARNING: Interrupting active process. Saving current progress...")
        
        # Ensure data is saved before exit
        downloader._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, downloader.downloaded_cards_data)
        downloader._update_sets_completion()
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: Progress saved. Shutting down gracefully. 👋")
        exit(0) 

if __name__ == "__main__":
    main()
