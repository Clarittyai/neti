"""The pure core: no I/O, no clock, no environment, no randomness.

Everything here is a function of its arguments. Resolvers do the I/O and hand in `Resolution`
objects; this package turns those into a verdict and a record. The separation is what makes a stored
decision replayable, and it is enforced by tests/property/test_core_is_pure.py.
"""
