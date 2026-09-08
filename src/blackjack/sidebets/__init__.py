"""Side bets, evaluated by exact enumeration from paytables in config.

Two layers: rank-only for bets that cannot see suits, and a full 52-card-type
layer for those that can. See :mod:`blackjack.sidebets.base`.
"""
