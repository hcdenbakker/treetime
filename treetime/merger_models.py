"""
methods to calculate merger models for a time tree
"""
from __future__ import print_function, division
import numpy as np
import scipy.special as sf
from scipy.interpolate import interp1d
from Bio import AlignIO, Phylo
from scipy.interpolate import interp1d
from collections import Iterable
import config as ttconf


class Coalescent(object):
    """docstring for Coalescent"""
    def __init__(self, tree, Tc=0.001, logger=None, date2dist=None):
        super(Coalescent, self).__init__()
        self.tree = tree
        self.calc_branch_count()
        self.set_Tc(Tc)
        self.date2dist = date2dist
        if logger is None:
            def f(*args):
                print(*args)
            self.logger = f
        else:
            self.logger = logger

    def set_Tc(self, Tc, T=None):
        if isinstance(Tc, Iterable):
            if len(Tc)==len(T):
                x = np.concatenate(([-ttconf.BIG_NUMBER], T, [ttconf.BIG_NUMBER]))
                y = np.concatenate(([Tc[0]], Tc, [Tc[-1]]))
                self.Tc = interp1d(x,y)
            else:
                self.logger("need Tc values and Timepoints of equal length",2,warn=True)
                self.Tc = interp1d([-ttconf.BIG_NUMBER, ttconf.BIG_NUMBER], [1e-5, 1e-5])
        else:
            self.Tc = interp1d([-ttconf.BIG_NUMBER, ttconf.BIG_NUMBER], [Tc, Tc])
        self.calc_integral_merger_rate()


    def calc_branch_count(self):
        # make a list of (time, merger or loss event) by root first
        self.tree_events = np.array(sorted([(n.time_before_present, len(n.clades)-1)
                                for n in self.tree.find_clades()], key=lambda x:-x[0]))

        # collapse multiple events at one time point into sum of changes
        from collections import defaultdict
        dn_branch = defaultdict(int)
        for (t, dn) in self.tree_events:
            dn_branch[t]+=dn
        unique_mergers = np.array(sorted(dn_branch.items(), key = lambda x:-x[0]))

        # calculate the branch count at each point summing the delta branch counts
        nbranches = [[ttconf.BIG_NUMBER, 1], [unique_mergers[0,0]+ttconf.TINY_NUMBER, 1]]
        for ti, (t, dn) in enumerate(unique_mergers[:-1]):
            new_n = nbranches[-1][1]+dn
            next_t = unique_mergers[ti+1,0]+ttconf.TINY_NUMBER
            nbranches.append([t, new_n])
            nbranches.append([next_t, new_n])

        new_n += unique_mergers[-1,1]
        nbranches.append([next_t, new_n])
        nbranches.append([-ttconf.BIG_NUMBER, new_n])
        nbranches=np.array(nbranches)

        self.nbranches = interp1d(nbranches[:,0], nbranches[:,1], kind='linear')


    def calc_integral_merger_rate(self):
        # integrate the piecewise constant branch count function.
        tvals = np.unique(self.nbranches.x[1:-1])
        rate = self.branch_merger_rate(tvals)
        avg_rate = 0.5*(rate[1:] + rate[:-1])
        cost = np.concatenate(([0],np.cumsum(np.diff(tvals)*avg_rate)))
        # make interpolation objects for the branch count and its integral
        # the latter is scaled by 0.5/Tc
        # need to add extra point at very large time before present to
        # prevent 'out of interpolation range' errors
        self.integral_merger_rate = interp1d(np.concatenate(([-ttconf.BIG_NUMBER], tvals,[ttconf.BIG_NUMBER])),
                                  np.concatenate(([cost[0]], cost,[cost[-1]])), kind='linear')

    def branch_merger_rate(self, t):
        # note that we always have a positive merger rate by capping the
        # number of branches at 0.5 from below. in these regions, the
        # function should only be called if the tree changes.
        return np.maximum(0.5,self.nbranches(t)-1.0)/self.Tc(t)

    def total_merger_rate(self, t):
        # not that we always have a positive merger rate by capping the
        # number of branches at 0.5 from below. in these regions, the
        # function should only be called if the tree changes.
        nlineages = np.maximum(0.5,self.nbranches(t)-1.0)
        return nlineages*(nlineages+1)/self.Tc(t)


    def cost(self, t_node, branch_length, multiplicity=2.0):
        # return the cost associated with a branch starting at t_node
        # t_node is time before present, the branch goes back in time
        merger_time = t_node+branch_length
        return self.integral_merger_rate(merger_time) - self.integral_merger_rate(t_node)\
                 - np.log(self.total_merger_rate(merger_time))*(multiplicity-1.0)/multiplicity


    def attach_to_tree(self):
        for clade in self.tree.find_clades():
            if clade.up is not None:
                clade.branch_length_interpolator.merger_cost = self.cost


    def total_LH(self):
        LH = 0.0 #np.log(self.total_merger_rate([node.time_before_present for node in self.tree.get_nonterminals()])).sum()
        for node in self.tree.find_clades():
            if node.up:
                LH -= self.cost(node.time_before_present, node.branch_length)
        return LH

    def optimize_Tc(self):
        from scipy.optimize import minimize_scalar
        initial_Tc = self.Tc
        def cost(Tc):
            self.set_Tc(Tc)
            return -self.total_LH()

        sol = minimize_scalar(cost, bounds=[ttconf.TINY_NUMBER,10.0])
        if sol["success"]:
            self.set_Tc(sol['x'])
        else:
            self.set_Tc(initial_Tc.y, T=initial_Tc.x)


    def optimize_skyline(self, n_points=20, stiffness=2.0, method = 'SLSQP',
                         tol=0.03, regularization=10.0, **kwarks):
        '''
        optimize the trajectory of the merger rate 1./T_c to maximize the
        coalescent likelihood.
        parameters:
            n_points    --  number of pivots of the Tc interpolation object
            stiffness   --  penalty for rapid changes in log(Tc)
            methods     --  method used to optimize
            tol         --  optimization tolerance
            regularization --  cost of moving logTc outsize of the range [-100,0]
                               merger rate is measured in branch length units, no
                               plausible rates should never be outside this window
        '''
        self.logger("Coalescent:optimize_skyline:... current LH: %f"%self.total_LH(),2)
        from scipy.optimize import minimize
        initial_Tc = self.Tc
        tvals = np.linspace(self.tree_events[0,0], self.tree_events[-1,0], n_points)
        def cost(logTc):
            # cap log Tc to avoid under or overflow and nan in logs
            self.set_Tc(np.exp(np.maximum(-200,np.minimum(100,logTc))), tvals)
            neglogLH = -self.total_LH() + stiffness*np.sum(np.diff(logTc)**2) \
                       + np.sum((logTc>0)*logTc*regularization)\
                       - np.sum((logTc<-100)*logTc*regularization)
            return neglogLH

        sol = minimize(cost, np.ones_like(tvals)*np.log(self.Tc.y.mean()), method=method, tol=tol)
        if sol["success"]:
            dlogTc = 0.1
            opt_logTc = sol['x']
            dcost = []
            for ii in range(len(opt_logTc)):
                tmp = opt_logTc.copy()
                tmp[ii]+=dlogTc
                cost_plus = cost(tmp)
                tmp[ii]-=2*dlogTc
                cost_minus = cost(tmp)
                dcost.append([cost_minus, cost_plus])

            dcost = np.array(dcost)
            optimal_cost = cost(opt_logTc)
            self.confidence = -dlogTc/(2*optimal_cost - dcost[:,0] - dcost[:,1])
            self.logger("Coalescent:optimize_skyline:...done. new LH: %f"%self.total_LH(),2)
        else:
            self.set_Tc(initial_Tc.y, T=initial_Tc.x)
            self.logger("Coalescent:optimize_skyline:...failed",2, warn=True)


    def skyline_empirical(self, gen=1.0, n_points = 20):
        '''
        returns the skyline, i.e., an estimate of the inverse rate of coalesence.
        Here, the skyline is estimated from a sliding window average of the observed
        mergers, i.e., without reference to the coalescence likelihood.
        parameters:
            gen -- number of generations per year.
        '''

        mergers = self.tree_events[:,1]>0
        merger_tvals = self.tree_events[mergers,0]
        nlineages = self.nbranches(merger_tvals-ttconf.TINY_NUMBER)
        expected_merger_density = nlineages*(nlineages-1)*0.5

        nmergers = len(mergers)
        et = merger_tvals
        ev = 1.0/expected_merger_density
        # reduce the window size if there are few events in the tree
        if 2*n_points>len(expected_merger_density):
            n_points = len(ev)//4

        # smoothes with a sliding window over data points
        avg = np.sum(ev)/np.abs(et[0]-et[-1])
        dt = et[0]-et[-1]
        mid_points = np.concatenate(([et[0]-0.5*(et[1]-et[0])],
                                      0.5*(et[1:] + et[:-1]),
                                     [et[-1]+0.5*(et[-1]-et[-2])]))

        # this smoothes the ratio of expected and observed merger rate
        self.Tc_inv = interp1d(mid_points[n_points:-n_points],
                        [np.sum(ev[(et>=l)&(et<u)])/(u-l+dt/nmergers)
                        for u,l in zip(mid_points[:-2*n_points],mid_points[2*n_points:])])

        return interp1d(self.date2dist.to_numdate(self.Tc_inv.x), gen/self.date2dist.slope/self.Tc_inv.y)


    def skyline_inferred(self, gen=1.0, confidence=False):
        '''
        return the skyline, i.e., an estimate of the inverse rate of coalesence.
        This function merely returns the merger rate self.Tc that was set or
        estimated by other means. If it was determined using self.optimize_skyline,
        the returned skyline will maximize the coalescent likelihood.
        parameters:
            gen -- number of generations per year. Unit of time is branch length,
                   hence this needs to be the inverse substitution rate per generation
            confidence -- False, or number of standard deviations of confidence intervals
        '''
        skyline = interp1d(self.date2dist.to_numdate(self.Tc.x[1:-1]), gen/self.date2dist.slope*self.Tc.y[1:-1])
        if confidence and hasattr(self, 'confidence'):
            conf = [skyline.y*np.exp(-confidence*self.confidence), skyline.y*np.exp(confidence*self.confidence)]
            return skyline, conf
        else:
            return skyline




def traveling_wave(tree, Tc=None, tau=None):
    '''
    assigns coalescent merger rates to all branches in the tree
    '''
    #
    if tau is None:
        tau = Tc/8.0   # 8 is roughly the factor between total coalescence and expansion rate in realistic populations
    # determine the msg to parents
    for n in tree.find_clades(order='postorder'):
        n._polarizer_to_parent = np.sum([c._polarizer_to_parent for c in n.clades])
        n._polarizer_to_parent*= np.exp(-n.branch_length/tau)
        n._polarizer_to_parent+=(1-np.exp(-n.branch_length/tau))*tau

    # determine the msg to children
    tree.root._polarizer_from_parent = 0.0
    for n in tree.get_nonterminals(order='preorder'):
        tmp = np.sum([c._polarizer_to_parent for c in n.clades]) + n._polarizer_from_parent
        for c in n.clades:
            c._polarizer_from_parent = tmp-c._polarizer_to_parent
            c._polarizer_from_parent*= np.exp(-c.branch_length/tau)
            c._polarizer_from_parent+=(1-np.exp(-c.branch_length/tau))*tau

    # determine the msg to parents
    for n in tree.find_clades(order='postorder'):
        n.lbi = n._polarizer_from_parent + np.sum([c._polarizer_to_parent for c in n.clades])

    # assign those rates to all nodes in the tree
    for n in tree.find_clades():
        n.merger_rate = n.lbi/tau/Tc

