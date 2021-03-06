"""
Test time tree class
"""

import unittest,os,sys
from Bio import AlignIO

from ..treetime import treeanc

resources_dir = os.path.join(path.dirname(__file__), '../data/')

class TestTreeAnc(unittest.TestCase):
    def test_read_newick(self):
        anc_t = treeanc.TreeAnc.from_file(resources_dir+'PR.B.100.nwk', 'newick')
        assert len(anc_t.tree.get_terminals()) == 100 #  tree read successfully

    def testset_additional_tree_params(self):
        #  up-link installed succesfully
        anc_t = treeanc.TreeAnc.from_file(resources_dir+'PR.B.100.nwk', 'newick')
        for node in anc_t.tree.get_nonterminals():
            for ch in node.clades:
                assert ch.up == node

    def test_aln_to_leaves(self):
        anc_t = treeanc.TreeAnc.from_file(resources_dir+'PR.B.100.nwk', 'newick')
        aln = AlignIO.read(resources_dir+'PR.B.100.fasta', 'fasta')

        err = anc_t.load_aln(aln)
        assert (err==0) # all sequencs were set up successfully

    def test_optimize_(self):
        pass


suite = unittest.TestLoader().loadTestsFromTestCase(TestTreeAnc)
unittest.TextTestRunner(verbosity=10).run(suite)

if __name__=='__main__':
    pass
