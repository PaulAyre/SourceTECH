"""
PavTECH API Client for SourceTECH.
Handles batch file processing through PavTECH's batch API endpoints.
"""
import requests
import time
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class PavTechClient:
    """
    Client for PavTECH Batch API.

    Flow:
    1. Upload files via /api/batch/upload
    2. Start processing via /api/batch/process_all
    3. Poll /api/batch/status until all_complete
    4. Download master document via /api/batch/download_master
    """

    def __init__(self, base_url: str, temp_dir: str = '/tmp/sourcetech'):
        self.base_url = base_url.rstrip('/')
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Polling config
        self.poll_interval = 3  # seconds
        self.max_poll_time = 600  # 10 minutes max

    def process_batch(self, file_paths: List[Path], vendor_name: str) -> Tuple[bool, Dict]:
        """
        Process multiple files through PavTECH as a batch.

        Args:
            file_paths: List of paths to cleaned files
            vendor_name: Used as vendor_name for PavTECH batch

        Returns:
            Tuple of (success: bool, result: dict)
            On success, result contains:
                - batch_id
                - file_results: dict of file_id -> status
                - master_document_path (local)
                - total_valuation
                - total_policies
            On failure, result contains:
                - error: error message
                - partial_results: any files that did complete
        """
        if not file_paths:
            return False, {'error': 'No files to process'}

        try:
            # Step 1: Upload all files to PavTECH
            upload_result = self._upload_batch(file_paths, vendor_name)
            if not upload_result.get('success'):
                return False, {'error': upload_result.get('error', 'Upload failed')}

            batch_id = upload_result['batch_id']
            file_ids = upload_result['file_ids']
            logger.info(f"Uploaded batch to PavTECH: {batch_id} with {len(file_ids)} files")

            # Step 2: Start processing all files (one by one)
            process_result = self._start_processing(vendor_name, batch_id, file_ids)
            if not process_result.get('success'):
                logger.warning(f"Process start returned: {process_result}")
                # Continue anyway - some files may have started

            logger.info(f"Started processing {len(process_result.get('started', []))} files")

            # Step 3: Poll until all complete
            final_status = self._poll_until_complete(vendor_name, batch_id)

            if not final_status.get('all_complete'):
                # Partial success - some files may have completed
                return False, {
                    'error': 'Not all files completed processing',
                    'batch_id': batch_id,
                    'partial_results': final_status.get('files', {}),
                    'complete_count': final_status.get('complete_files', 0),
                    'total_count': final_status.get('total_files', 0)
                }

            logger.info(f"PavTECH batch processing complete: {batch_id}")

            # Step 4: Generate master document
            generate_result = self._generate_master(vendor_name, batch_id)
            if not generate_result.get('success'):
                logger.warning(f"Master generation returned: {generate_result}")
                # Continue anyway - might already exist

            # Step 5: Download master document
            master_path = self._download_master(vendor_name, batch_id)
            if not master_path:
                return False, {
                    'error': 'Failed to download master document',
                    'batch_id': batch_id,
                    'file_results': final_status.get('files', {})
                }

            # Calculate totals
            files = final_status.get('files', {})
            total_valuation = sum(f.get('valuation', 0) for f in files.values())
            total_policies = sum(f.get('records', 0) for f in files.values())

            return True, {
                'batch_id': batch_id,
                'file_results': files,
                'master_document_path': str(master_path),
                'total_valuation': total_valuation,
                'total_policies': total_policies,
                'file_count': len(files)
            }

        except Exception as e:
            logger.error(f"PavTECH batch processing error: {e}")
            return False, {'error': str(e)}

    def _upload_batch(self, file_paths: List[Path], vendor_name: str) -> Dict:
        """Upload multiple files to PavTECH /api/batch/upload endpoint."""
        try:
            files = []
            for path in file_paths:
                files.append(('files', (path.name, open(path, 'rb'))))

            data = {'vendor_name': vendor_name}

            response = requests.post(
                f"{self.base_url}/api/batch/upload",
                files=files,
                data=data,
                timeout=120  # Longer timeout for multiple files
            )

            # Close file handles
            for _, (_, f) in files:
                f.close()

            if response.status_code == 200:
                result = response.json()
                if result.get('batch_id'):
                    return {
                        'success': True,
                        'batch_id': result['batch_id'],
                        'file_ids': result.get('file_ids', [])
                    }
                else:
                    logger.error(f"Upload response missing batch_id: {result}")
                    return {'success': False, 'error': 'No batch_id in response'}

            logger.error(f"Upload failed: {response.status_code} - {response.text[:500]}")
            return {'success': False, 'error': f'Upload failed: {response.status_code}'}

        except requests.RequestException as e:
            logger.error(f"Upload request error: {e}")
            return {'success': False, 'error': str(e)}

    def _start_processing(self, vendor_name: str, batch_id: str, file_ids: List[str]) -> Dict:
        """Start processing all files in batch via /api/batch/process_file for each file."""
        results = {'success': True, 'started': [], 'failed': []}

        for file_id in file_ids:
            try:
                response = requests.post(
                    f"{self.base_url}/api/batch/process_file",
                    json={
                        'vendor_name': vendor_name,
                        'batch_id': batch_id,
                        'file_id': file_id,
                        'company_name': ''  # Let PavTECH auto-detect from filename
                    },
                    timeout=30
                )

                if response.status_code == 200:
                    results['started'].append(file_id)
                    logger.info(f"Started processing {file_id}")
                else:
                    results['failed'].append(file_id)
                    logger.warning(f"Process start failed for {file_id}: {response.status_code}")

            except requests.RequestException as e:
                results['failed'].append(file_id)
                logger.warning(f"Process start error for {file_id}: {e}")

        if results['failed']:
            results['success'] = len(results['started']) > 0  # Partial success

        return results

    def _poll_until_complete(self, vendor_name: str, batch_id: str) -> Dict:
        """Poll /api/batch/status until all files complete or timeout."""
        start_time = time.time()
        last_complete = 0

        while (time.time() - start_time) < self.max_poll_time:
            try:
                response = requests.get(
                    f"{self.base_url}/api/batch/status",
                    params={
                        'vendor_name': vendor_name,
                        'batch_id': batch_id
                    },
                    timeout=10
                )

                if response.status_code == 200:
                    status = response.json()
                    complete = status.get('complete_files', 0)
                    total = status.get('total_files', 0)
                    all_complete = status.get('all_complete', False)

                    # Log progress changes
                    if complete != last_complete:
                        logger.info(f"Batch {batch_id}: {complete}/{total} files complete")
                        last_complete = complete

                    if all_complete:
                        return status

                    # Check for errors in all files
                    files = status.get('files', {})
                    error_count = sum(1 for f in files.values() if f.get('status') == 'error')
                    if error_count == total and total > 0:
                        logger.error(f"All files errored in batch {batch_id}")
                        return status

                elif response.status_code == 404:
                    return {'error': 'Batch not found'}

            except requests.RequestException as e:
                logger.warning(f"Poll error (will retry): {e}")

            time.sleep(self.poll_interval)

        return {'error': 'Processing timeout', 'all_complete': False}

    def _generate_master(self, vendor_name: str, batch_id: str) -> Dict:
        """Request PavTECH to generate the master document via /api/batch/generate_master."""
        try:
            response = requests.post(
                f"{self.base_url}/api/batch/generate_master",
                json={
                    'vendor_name': vendor_name,
                    'batch_id': batch_id
                },
                timeout=120  # Master generation can take a while
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Master document generated for batch {batch_id}")
                return {'success': True, **result}

            logger.warning(f"Master generation failed: {response.status_code}")
            return {'success': False, 'error': f'Status {response.status_code}'}

        except requests.RequestException as e:
            logger.warning(f"Master generation error: {e}")
            return {'success': False, 'error': str(e)}

    def _download_master(self, vendor_name: str, batch_id: str) -> Optional[Path]:
        """Download master document from PavTECH."""
        try:
            response = requests.get(
                f"{self.base_url}/api/batch/download_master",
                params={
                    'vendor_name': vendor_name,
                    'batch_id': batch_id
                },
                timeout=120,
                stream=True
            )

            if response.status_code == 200:
                # Get filename from Content-Disposition header
                content_disp = response.headers.get('Content-Disposition', '')
                if 'filename=' in content_disp:
                    if 'filename="' in content_disp:
                        filename = content_disp.split('filename="')[1].split('"')[0]
                    else:
                        filename = content_disp.split('filename=')[1].split(';')[0].strip()
                else:
                    filename = f"master_{batch_id}.xlsx"

                master_path = self.temp_dir / filename

                with open(master_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                logger.info(f"Downloaded master document: {master_path}")
                return master_path

            logger.error(f"Master download failed: {response.status_code}")
            return None

        except requests.RequestException as e:
            logger.error(f"Download request error: {e}")
            return None

    def health_check(self) -> bool:
        """Check if PavTECH is available."""
        try:
            response = requests.get(f"{self.base_url}/", timeout=5)
            return response.status_code == 200
        except:
            return False

    # Legacy single-file method for backward compatibility
    def process_file(self, file_path: Path, vendor_name: str) -> Tuple[bool, Dict]:
        """Process a single file (wraps process_batch for compatibility)."""
        return self.process_batch([file_path], vendor_name)
