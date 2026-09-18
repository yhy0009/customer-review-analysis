"""Download all filtered snapshot rows, with stable content and public fields."""

import csv
import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.serve_dashboard import seed_demo
from src.dashboard_server import create_server
from src.errors import OutputError
from src.models import DuplicatePolicy, RawReview, ReviewFilter
from src.sqlite_repository import SQLiteReviewRepository
from src.web_dashboard import DashboardData


class DashboardExportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database, _ = seed_demo(self.root)
        self.data = DashboardData(self.database)
        self.server = create_server(self.data, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_all_pages_and_both_formats_are_stable_after_database_changes(self):
        snapshot = self.data.create_snapshot(ReviewFilter(), 2).response
        self.assertEqual(len(snapshot['page']['items']), 8)
        self.assertEqual(snapshot['export']['row_count'], 18)
        urls = snapshot['export']['urls']
        original = {fmt: self.get(url) for fmt, url in urls.items()}
        for fmt, (code, headers, payload) in original.items():
            self.assertEqual(code, 200)
            self.assertIn(f'reviews.{fmt}', headers['Content-Disposition'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
        rows = [json.loads(row) for row in original['jsonl'][2].decode().splitlines()]
        self.assertEqual([row['id'] for row in rows], list(range(1, 19)))
        self.assertTrue(all(row['source_review_id'] is None for row in rows))
        self.assertTrue(all('source_file' not in row and 'raw_payload' not in row for row in rows))
        csv_rows = list(csv.DictReader(io.StringIO(original['csv'][2].decode('utf-8-sig'))))
        self.assertEqual([int(row['id']) for row in csv_rows], list(range(1, 19)))
        with SQLiteReviewRepository(self.database) as repository:
            detail = repository.get_review(1)
            repository.save_clean_reviews([replace(detail.review, review_text='변경한 원문')], DuplicatePolicy.UPSERT)
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        for fmt, url in urls.items():
            self.assertEqual(self.get(url)[2], original[fmt][2])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        current = self.data.create_snapshot(ReviewFilter()).response
        fresh = [json.loads(row) for row in self.get(current['export']['urls']['jsonl'])[2].decode().splitlines()]
        self.assertEqual(fresh[0]['review_text'], '변경한 원문')
        self.assertIsNone(fresh[0]['sentiment'])

    def test_rating_filter_exports_whole_scope_and_csv_keeps_formula_safety(self):
        with SQLiteReviewRepository(self.database) as repository:
            detail = repository.get_review(1)
            repository.save_clean_reviews([replace(detail.review, review_text='=SUM(1,2)',
                                                  source_review_id='private-source-id')], DuplicatePolicy.UPSERT)
        snapshot = self.data.create_snapshot(ReviewFilter(rating=5)).response
        code, headers, content = self.get(snapshot['export']['urls']['csv'])
        self.assertEqual(code, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/csv'))
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), snapshot['statistics']['total_reviews'])
        self.assertTrue(all(row['rating'] == '5' for row in rows))
        self.assertEqual(rows[0]['review_text'], "'=SUM(1,2)")
        code, headers, content = self.get(snapshot['export']['urls']['jsonl'])
        self.assertTrue(headers['Content-Type'].startswith('application/x-ndjson'))
        self.assertEqual(json.loads(content.splitlines()[0])['review_text'], '=SUM(1,2)')
        self.assertNotIn(b'private-source-id', content)

    def test_selection_crosses_repository_page_boundaries(self):
        with SQLiteReviewRepository(self.database) as repository:
            template = repository.get_review(1).review
            repository.save_raw_reviews([RawReview(source_review_id=f'extra-{i}') for i in range(503)],
                                        DuplicatePolicy.SKIP)
            repository.save_clean_reviews([replace(template, id=i, review_text=f'추가 리뷰 {i}')
                                           for i in range(19, 522)], DuplicatePolicy.SKIP)
        snapshot = self.data.create_snapshot(ReviewFilter(), 2)
        self.assertEqual(snapshot.response['export']['row_count'], 521)
        self.assertEqual([detail.review.id for detail in snapshot.export_reviews], list(range(1, 522)))

    def test_limit_does_not_return_truncated_data_and_empty_formats_are_valid(self):
        with patch('src.web_dashboard.EXPORT_LIMIT', 17):
            snapshot = self.data.create_snapshot(ReviewFilter()).response
        self.assertEqual(snapshot['export'], {'status': 'too_large', 'row_count': 18, 'limit': 17, 'urls': {}})
        self.assertIsNone(self.data.get_snapshot(snapshot['snapshot_id']).export_reviews)
        self.assertEqual(self.get(f"/api/export/{snapshot['snapshot_id']}/csv")[0], 413)
        # Exactly the limit is permitted.
        with patch('src.web_dashboard.EXPORT_LIMIT', 18):
            self.assertEqual(self.data.create_snapshot(ReviewFilter()).response['export']['status'], 'available')
        empty = self.data.create_snapshot(ReviewFilter(product_name='no matches')).response
        self.assertEqual(self.get(empty['export']['urls']['jsonl'])[2], b'')
        self.assertEqual(len(self.get(empty['export']['urls']['csv'])[2].decode('utf-8-sig').splitlines()), 1)

    def test_expiry_format_filters_and_origin_are_enforced(self):
        snapshot = self.data.create_snapshot(ReviewFilter()).response
        path = snapshot['export']['urls']['jsonl']
        self.assertEqual(self.get(path + '?rating=1')[0], 400)
        self.assertEqual(self.get(path, Origin='https://foreign.example')[0], 403)
        self.assertEqual(self.get(path.replace('/jsonl', '/xlsx'))[0], 404)
        self.data.ttl = -1
        self.assertEqual(self.get(path)[0], 410)
        self.assertEqual(self.get('/api/export/unknown/csv')[0], 410)

    def test_failed_export_cleans_partial_files_and_can_retry_same_snapshot(self):
        snapshot = self.data.create_snapshot(ReviewFilter()).response
        path = snapshot['export']['urls']['csv']
        output_paths = []

        def fail_after_write(reviews, output_path, **kwargs):
            output_paths.append(output_path)
            output_path.write_text('partial data', encoding='utf-8')
            raise OutputError(f'private output path: {output_path}')

        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with patch('src.exporter.FileReviewExporter.export_reviews', side_effect=fail_after_write):
            code, headers, content = self.get(path)
        self.assertEqual(code, 503)
        self.assertNotIn('Content-Disposition', headers)
        self.assertNotIn(b'private output path', content)
        self.assertEqual(len(output_paths), 1)
        self.assertNotIn(str(output_paths[0]).encode(), content)
        self.assertFalse(output_paths[0].parent.exists())
        code, _, content = self.get(path)
        self.assertEqual(code, 200)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))), 18)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
