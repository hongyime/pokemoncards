import os
import json
import csv
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path, PureWindowsPath
import tempfile
from typing import List, Dict, Any, Optional, Tuple
import sys

# --- Configuration ---
API_BASE_URL = "https://api.pokemontcg.io/v2"
SETS_ENDPOINT = f"{API_BASE_URL}/sets"
SETS_CACHE_FILE = "sets_cache.json"
DOWNLOADED_SETS_CSV = "downloaded_sets.csv"
DOWNLOADED_CARDS_CSV = "downloaded_cards.csv"
MAX_RETRIES = 0 # Can be 0 for no retries
MAX_WORKERS = 10 # Maximum running or queued image requests
MAX_FAILED_CARDS = 3
CHECKPOINT_BATCH_SIZE = 50

# --- CSV Headers Definition (Ensuring backward compatibility) ---
SETS_HEADERS = [
    'set_id', 'set_name', 'printed_total', 'last_updated', 'last_checked',
    'cards_downloaded', 'set_complete'
]
CARDS_HEADERS = [
    'set_id', 'set_name', 'card_number', 'filename', 'download_date',
    'image_url', 'is_hires', 'file_size', 'download_duration', 'status'
]

class CheckpointError(ValueError):
    """Existing checkpoint data must be repaired before writes can resume."""


class PokemonCardDownloader:
    """
    A professional, resilient, and parallelized utility for downloading Pokémon card images
    and tracking collection progress.
    """
    def __init__(self):
        """Initializes the downloader and establishes the initial state."""
        self.stop_script = False
        self._checkpoint_write_failed = False
        self.image_dir: Optional[str] = None
        self.downloaded_sets_data: Dict[str, Dict[str, Any]] = self._load_csv_data(DOWNLOADED_SETS_CSV, 'set_id')
        self.downloaded_cards_data: Dict[Tuple[str, str], Dict[str, Any]] = self._load_csv_data(DOWNLOADED_CARDS_CSV, ('set_id', 'card_number'))
        self.api_sets_data: Dict[str, Dict[str, Any]] = {}

    def _load_csv_data(self, filename: str, key_field: Any) -> Dict:
        """Read an entire valid checkpoint, preserving additional columns."""
        if not os.path.exists(filename):
            return {}
        fields = (key_field,) if isinstance(key_field, str) else key_field
        data = {}
        try:
            with open(filename, newline='', encoding='utf-8-sig') as stream:
                reader = csv.DictReader(stream, strict=True)
                headers = reader.fieldnames
                if not headers or any(not h for h in headers) or len(set(headers)) != len(headers):
                    raise ValueError('Missing or duplicate column names')
                required = SETS_HEADERS if filename == DOWNLOADED_SETS_CSV else CARDS_HEADERS
                if not set(required).issubset(headers):
                    raise ValueError('Missing required checkpoint columns')
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError('Incomplete or extra CSV fields')
                    if any(not row[field].strip() for field in fields):
                        raise ValueError('Empty record key')
                    key = row[key_field] if isinstance(key_field, str) else tuple(row[f] for f in fields)
                    if key in data:
                        raise ValueError('Duplicate record key')
                    data[key] = row
            return data
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            self.stop_script = True
            raise CheckpointError(f'Cannot safely read {filename}; original file was preserved: {error}') from error

    def _save_csv_data(self, filename: str, headers: List[str], data: Dict) -> bool:
        """Writes the current dictionary data back to a CSV file (atomic write for resilience)."""
        if self._checkpoint_write_failed:
            return False
        if not data:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] WARNING: Skipping save for {filename}. Data dictionary is empty.")
            return True

        extra_headers = dict.fromkeys(key for row in data.values() for key in row if key not in headers)
        output_headers = list(headers) + list(extra_headers)
        temp_filename = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             dir=os.path.dirname(os.path.abspath(filename)),
                                             prefix='.' + os.path.basename(filename) + '.', suffix='.tmp',
                                             delete=False) as f:
                temp_filename = f.name
                writer = csv.DictWriter(f, fieldnames=output_headers)
                writer.writeheader()
                for row in data.values():
                    # Ensure only defined headers are written
                    filtered_row = {k: row.get(k, '') for k in output_headers}
                    writer.writerow(filtered_row)
                f.flush()
                os.fsync(f.fileno())

            # Atomic file replacement for resilience
            os.replace(temp_filename, filename)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCCESS: Progress saved to {filename}.")
            return True
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL: Failed to save progress to {filename}. {e}")
            self.stop_script = True
            self._checkpoint_write_failed = True
            return False
        finally:
            if temp_filename and os.path.exists(temp_filename):
                os.remove(temp_filename)

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

    @staticmethod
    def _card_path(card_data: Dict[str, Any], root_dir: str) -> Path:
        """Keep existing folder names, but reject ambiguous or escaping paths."""
        root = Path(root_dir).resolve()
        parts = [card_data.get('set_name'), card_data.get('filename')]
        for part in parts:
            if (not isinstance(part, str) or not part or part in ('.', '..')
                    or part.endswith((' ', '.')) or PureWindowsPath(part).is_reserved()
                    or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in part)):
                raise ValueError('Set name and filename must be single safe path components')
        folder = root / parts[0]
        candidate = folder / parts[1]
        if folder.is_symlink() or candidate.is_symlink():
            raise ValueError('Image paths must not use symbolic links')
        candidate.resolve().relative_to(root)
        return candidate

    @staticmethod
    def _image_complete(path: Path, expected_size: Any = None) -> bool:
        """Check PNG boundaries and an available recorded size, without decoding pixels."""
        try:
            size = path.stat().st_size
            if size < 45:
                return False
            if expected_size not in (None, '', '0', 0) and size != int(expected_size):
                return False
            with path.open('rb') as stream:
                if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                    return False
                stream.seek(-12, os.SEEK_END)
                return stream.read() == b'\x00\x00\x00\x00IEND\xaeB`\x82'
        except (OSError, ValueError, TypeError):
            return False

    def _download_card(self, card_key: Tuple[str, str], card_data: Dict[str, Any], root_dir: str) -> bool:
        """Promote a completed image atomically, retaining the prior file on failure."""
        if self.stop_script:
            return False
        try:
            save_path = self._card_path(card_data, root_dir)
            if self._image_complete(save_path, card_data.get('file_size')):
                card_data.update(status='success', file_size=save_path.stat().st_size)
                return True
            save_path.parent.mkdir(parents=True, exist_ok=True)
        except (ValueError, TypeError, OSError) as error:
            card_data['status'] = 'failed (Invalid image path)'
            print(f'Cannot use image path for {card_key}: {error}')
            return False

        for attempt in range(MAX_RETRIES + 1):
            if self.stop_script:
                return False
            if attempt:
                time.sleep(min(2 ** attempt, 30))
                if self.stop_script:
                    return False
            temporary = None
            try:
                started = time.monotonic()
                with requests.get(card_data['image_url'], stream=True, timeout=30) as response:
                    if response.status_code == 429:
                        self.stop_script = True
                        card_data['status'] = 'failed (Rate limited; resume later)'
                        return False
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile(mode='wb', dir=save_path.parent,
                                                     prefix='.' + save_path.name + '.', suffix='.part',
                                                     delete=False) as stream:
                        temporary = Path(stream.name)
                        for chunk in response.iter_content(chunk_size=8192):
                            if self.stop_script:
                                return False
                            stream.write(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                    length = response.headers.get('Content-Length')
                    if response.headers.get('Content-Encoding', 'identity') != 'identity':
                        length = None
                    if length is not None and temporary.stat().st_size != int(length):
                        raise ValueError('Image length does not match the response')
                    if not self._image_complete(temporary):
                        raise ValueError('Incomplete PNG image response')
                os.replace(temporary, save_path)
                card_data.update(status='success', download_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 file_size=save_path.stat().st_size,
                                 download_duration=round(time.monotonic() - started, 4))
                return True
            except requests.exceptions.RequestException as error:
                if error.response is not None and 400 <= error.response.status_code < 500:
                    break
            except (ValueError, OSError) as error:
                card_data['status'] = 'failed (Image or file error)'
                if isinstance(error, OSError):
                    self.stop_script = True
                print(f'Image was not replaced for {card_key}: {error}')
                return False
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        card_data['status'] = f'failed (Max Retries {MAX_RETRIES})'
        return False

    def _process_downloads(self) -> bool:
        """Bound outstanding work and save stable snapshots of completed results."""
        if self.stop_script:
            return False
        root_dir = self.image_dir
        # Check for conflicting destinations before any image writes begin.
        destinations = {}
        for key, row in self.downloaded_cards_data.items():
            try:
                destination = os.path.normcase(str(self._card_path(row, root_dir)))
            except (ValueError, TypeError, OSError):
                continue  # The worker reports invalid paths without requesting images.
            if destination in destinations:
                self.stop_script = True
                raise CheckpointError(f'Conflicting image destinations for {destinations[destination]} and {key}')
            destinations[destination] = key

        def pending_cards():
            for key, row in self.downloaded_cards_data.items():
                if row.get('status') == 'success':
                    try:
                        if self._image_complete(self._card_path(row, root_dir), row.get('file_size')):
                            continue
                    except (ValueError, TypeError, OSError):
                        pass
                yield key, row

        pending = iter(pending_cards())
        inflight = {}
        failed = set()
        changed_since_save = 0
        executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

        def accept(future, key, working):
            nonlocal changed_since_save
            if future.cancelled():
                return
            try:
                successful = future.result()
            except Exception:
                working['status'] = 'failed (Unexpected worker error)'
                successful = False
                self.stop_script = True
            if working != self.downloaded_cards_data[key]:
                self.downloaded_cards_data[key].update(working)
                changed_since_save += 1
            if not successful:
                failed.add(key)
                if len(failed) >= MAX_FAILED_CARDS:
                    self.stop_script = True

        try:
            while True:
                while not self.stop_script and len(inflight) < MAX_WORKERS:
                    try:
                        key, row = next(pending)
                    except StopIteration:
                        break
                    working = dict(row)  # Workers never mutate the checkpoint being saved.
                    inflight[executor.submit(self._download_card, key, working, root_dir)] = (key, working)
                if not inflight:
                    break
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    key, working = inflight.pop(future)
                    accept(future, key, working)
                if changed_since_save >= CHECKPOINT_BATCH_SIZE and not self._checkpoint_write_failed:
                    self._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, self.downloaded_cards_data)
                    changed_since_save = 0
                if self.stop_script:
                    for future in inflight:
                        future.cancel()
        except BaseException:
            self.stop_script = True
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            for future, (key, working) in inflight.items():
                accept(future, key, working)
            if changed_since_save and not self._checkpoint_write_failed:
                self._save_csv_data(DOWNLOADED_CARDS_CSV, CARDS_HEADERS, self.downloaded_cards_data)
            if not self._checkpoint_write_failed:
                self._update_sets_completion()
        if failed:
            print(f'{len(failed)} cards could not finish. Progress was checkpointed where possible; resume later.')
        return not failed and not self.stop_script

    def _update_sets_completion(self):
        """Checks if all cards in a set are 'success' and updates the set_complete status."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] INFO: Checking set completion status...")
        set_counts: Dict[str, List[int]] = {} # [downloaded_success_count, total_count]
        updated_sets = 0

        # 1. Tally successful downloads per set
        for card_data in self.downloaded_cards_data.values():
            set_id = card_data['set_id']
            if set_id not in set_counts:
                # Use the printed_total from the sets CSV for the current total
                try:
                    total = int(self.downloaded_sets_data.get(set_id, {}).get('printed_total', 0))
                except (TypeError, ValueError):
                    total = 0  # Unknown totals must not mark a set complete.
                set_counts[set_id] = [0, total]
            
            if card_data.get('status') == 'success':
                if self.image_dir:
                    try:
                        if not self._image_complete(self._card_path(card_data, self.image_dir), card_data.get('file_size')):
                            continue
                    except (ValueError, TypeError, OSError):
                        continue
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
                if str(set_row.get('cards_downloaded', '')) != str(downloaded):
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
        if self.stop_script:
            return
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
        
        complete = self._process_downloads()
        
        if complete:
            print("\n========================================================")
            print("All required download and synchronization tasks are complete.")
            print("========================================================\n")
        elif not self.stop_script:
            print("Some images could not be downloaded. Saved progress is available to resume.")

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
    try:
        downloader = PokemonCardDownloader()
    except CheckpointError as error:
        print(f'Cannot start safely: {error}', file=sys.stderr)
        return

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
                # The download coordinator checkpoints completed work in its finally block.
                # Do not overwrite disk data after a failed read or an unrelated command error.
                break

    except KeyboardInterrupt:
        # **CRITICAL FIX**: Handle Ctrl+C for graceful exit and progress save
        print("\nInterrupted. Completed downloads were checkpointed where possible. Existing files remain available to resume.")

if __name__ == "__main__":
    main()
