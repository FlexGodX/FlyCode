"""Import a tiny table with the official schema; never distribute the dataset."""

import hashlib

import numpy as np
import pytest

from flycode.brain.full_connectome import importer
from flycode.brain.full_connectome.graph import SparseConnectome

pa = pytest.importorskip("pyarrow")
feather = pytest.importorskip("pyarrow.feather")


def source_files(tmp_path, monkeypatch):
    ids = np.array([720575940600000001, 720575940600000002, 720575940600000003], dtype=np.uint64)
    np.save(tmp_path / 'proofread_root_ids_783.npy', ids)
    table = pa.table({'pre_pt_root_id': [int(ids[0]), int(ids[0]), int(ids[1])],
                      'post_pt_root_id': [int(ids[1]), int(ids[1]), int(ids[2])],
                      'syn_count': [2, 3, 7], 'neuropil': ['A', 'B', 'A'],
                      'ach_avg': [1., 1., 0.], 'gaba_avg': [0., None, 1.], 'glut_avg': [0., 0., 0.],
                      'da_avg': [0., 0., 0.], 'ser_avg': [0., 0., 0.], 'oct_avg': [0., 0., 0.]})
    feather.write_feather(table, tmp_path / 'proofread_connections_783.feather')
    monkeypatch.setattr(importer, 'SOURCE_CHECKSUMS', {
        name: hashlib.md5((tmp_path / name).read_bytes()).hexdigest()
        for name in ('proofread_root_ids_783.npy', 'proofread_connections_783.feather')})


def test_import_normalizes_signed_connections_and_records_provenance(tmp_path, monkeypatch):
    source_files(tmp_path, monkeypatch)
    output = tmp_path / 'prepared'
    importer.prepare_flywire(tmp_path, output)
    graph = SparseConnectome.load(output)
    assert graph.weights[1, 0] == 1
    assert graph.weights[2, 1] == -1
    assert graph.metadata['release'] == '783'
    assert graph.metadata['source_files']['proofread_connections_783.feather']['sha256']
    assert graph.metadata['min_synapses'] == 5


def test_corrupt_source_is_rejected_before_import(tmp_path, monkeypatch):
    source_files(tmp_path, monkeypatch)
    (tmp_path / 'proofread_connections_783.feather').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        importer.prepare_flywire(tmp_path, tmp_path / 'output')


def test_download_verifies_bytes_and_keeps_verified_cache(tmp_path, monkeypatch):
    import io
    from urllib.error import URLError

    content = b'known test data'
    expected = hashlib.md5(content).hexdigest()
    monkeypatch.setattr(importer, 'SOURCE_CHECKSUMS', {'sample.feather': expected})
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: io.BytesIO(content))
    importer.download_flywire(tmp_path)
    assert (tmp_path / 'sample.feather').read_bytes() == content

    def offline(*args, **kwargs):
        raise URLError('offline')

    monkeypatch.setattr('urllib.request.urlopen', offline)
    importer.download_flywire(tmp_path)
    assert (tmp_path / 'sample.feather').read_bytes() == content


def test_invalid_download_does_not_replace_a_source_file(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(importer, 'SOURCE_CHECKSUMS', {'sample.feather': hashlib.md5(b'expected').hexdigest()})
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: io.BytesIO(b'wrong'))
    with pytest.raises(ValueError, match='checksum'):
        importer.download_flywire(tmp_path)
    assert not (tmp_path / 'sample.feather').exists()
