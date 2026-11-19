"""Only P1 StoredArtifact interface closure; imports old fixture definitions,
not the prior main/cap targets. Real dataclass, synthetic repository methods.
"""
from pathlib import Path
import sys,unittest
from dataclasses import replace
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
from test_dep_fasta_handoff import Fixture,resolve,h,Fault
from cyomics_projects.preprocess_repository import StoredArtifact

class ArtifactWrapperBoundary(unittest.TestCase):
 def test_original_wrapper_and_each_pin_boundary(self):
  fixture=Fixture()
  bundle=resolve(fixture.run())
  self.assertEqual(bundle.queries_fasta.count(b'\nACD\n'),4)
  self.assertEqual(fixture.log[-1],'fasta_owner_verified')
  original=StoredArtifact('row-map.tsv','text/tab-separated-values; charset=utf-8',fixture.rowmap,h.sha(fixture.rowmap))
  for label,value in [('raw_bytes_rejected',fixture.rowmap),
     ('wrong_name',replace(original,name='working-matrix.tsv')),
     ('wrong_mime',replace(original,media_type='application/octet-stream')),
     ('wrong_wrapper_sha',replace(original,sha256='0'*64)),
     ('wrong_body',replace(original,body=fixture.rowmap+b'bad\n'))]:
   with self.subTest(label):
    async def rowread(*args,**kwargs):fixture.log.append('rowmap');return value
    fixture.readers.processed.read_artifact=rowread;fixture.log=[]
    with self.assertRaises(Fault) as got:resolve(fixture.run())
    self.assertEqual((got.exception.status,got.exception.code),(503,'dep_fasta_binding_invalid'))
    self.assertNotIn('mapping',fixture.log);self.assertNotIn('fasta_owner_verified',fixture.log)

if __name__=='__main__':unittest.main(verbosity=2)
