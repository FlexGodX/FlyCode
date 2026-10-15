"""One-time import of the FlyWire Consortium v783 connectivity release."""

import hashlib
import urllib.request
from pathlib import Path

import numpy as np

from .graph import SparseConnectome, graph_from_edges

SOURCE_URL = 'https://zenodo.org/records/10676866'
SOURCE_CHECKSUMS = {
    'proofread_connections_783.feather': 'f48f972d262323a102aed49af1396b8a',
    'proofread_root_ids_783.npy': 'e0e6c19732fd8c7a4e39a2d170105421',
}
TRANSMITTERS = ('ach_avg', 'gaba_avg', 'glut_avg', 'oct_avg', 'ser_avg', 'da_avg')


def download_flywire(directory: Path) -> None:
    """Download only the two public connectivity files, with integrity checks."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in SOURCE_CHECKSUMS.items():
        target = directory / name
        if target.exists():
            digest = hashlib.md5()
            with target.open('rb') as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() == expected:
                continue
        temporary = target.with_suffix(target.suffix + '.part')
        url = f'https://zenodo.org/api/records/10676866/files/{name}/content'
        digest = hashlib.md5()
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as stream:
            while chunk := response.read(4 * 1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f'Download checksum mismatch: {name}')
        temporary.replace(target)


def prepare_flywire(source_dir: Path, output: Path, min_synapses: int = 5) -> SparseConnectome:
    """Verify official files, aggregate neuropils, then freeze a signed CSR graph.

    GABA/glutamate are modeled as inhibitory; other transmitters as excitatory.
    This is an explicit rate-model convention, not a measured functional sign.
    """
    import pyarrow as pa

    provenance = {}
    for name, expected in SOURCE_CHECKSUMS.items():
        md5, sha = hashlib.md5(), hashlib.sha256()
        with (source_dir / name).open('rb') as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                md5.update(chunk)
                sha.update(chunk)
        if md5.hexdigest() != expected:
            raise ValueError(f'Source checksum mismatch: {name}')
        provenance[name] = {'md5': expected, 'sha256': sha.hexdigest()}
    ids = np.sort(np.load(source_dir / 'proofread_root_ids_783.npy', allow_pickle=False).astype(np.uint64))
    nt_totals = np.zeros((len(ids), len(TRANSMITTERS)), dtype=np.float64)
    pre_chunks, post_chunks, count_chunks = [], [], []
    with pa.memory_map(str(source_dir / 'proofread_connections_783.feather'), 'r') as stream:
        reader = pa.ipc.open_file(stream)
        for index in range(reader.num_record_batches):
            batch = reader.get_batch(index)
            pre = batch.column('pre_pt_root_id').to_numpy().astype(np.uint64)
            post = batch.column('post_pt_root_id').to_numpy().astype(np.uint64)
            counts = batch.column('syn_count').to_numpy().astype(np.float32)
            sources = np.searchsorted(ids, pre)
            if np.any(sources >= len(ids)) or np.any(ids[np.minimum(sources, len(ids) - 1)] != pre):
                raise ValueError('Source neuron is not proofread')
            for column, name in enumerate(TRANSMITTERS):
                probability = np.nan_to_num(batch.column(name).to_numpy(zero_copy_only=False), nan=0.)
                if np.any((probability < 0) | (probability > 1)):
                    raise ValueError('Invalid neurotransmitter probability')
                nt_totals[:, column] += np.bincount(sources, weights=counts * probability, minlength=len(ids))
            pre_chunks.append(pre)
            post_chunks.append(post)
            count_chunks.append(counts)
    nt = np.argmax(nt_totals, axis=1)
    signs = np.where(np.isin(nt, [1, 2]), -1., 1.).astype(np.float32)
    graph = graph_from_edges(ids, np.concatenate(pre_chunks), np.concatenate(post_chunks),
                             np.concatenate(count_chunks), min_synapses=min_synapses, source_signs=signs,
                             metadata={'source': SOURCE_URL, 'release': '783', 'license': 'CC-BY-4.0',
                                       'source_files': provenance, 'neuron_universe': 'all released proofread root IDs',
                                       'sign_convention': 'dominant synapse-count-weighted presynaptic NT; '
                                                          'GABA/GLUT inhibitory, ACH/DA/OCT/SER excitatory approximation'})
    graph.save(output)
    return graph
