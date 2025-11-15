"""
Collection of "check" helpers used by the aa_bb BigBrother views.

Each module in this package encapsulates data gathering and rendering logic
for a different type of vetting signal (contacts, contracts, wallets, etc.).
Keeping the logic here makes it easy to import a single package and reuse
the shared helpers in templates, tasks, or admin actions.
"""
