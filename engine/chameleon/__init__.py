"""CHAMELEON layout engine.

Pipeline (see README for the full write-up):
    analyze  -> read the design's "DNA": units, roles, alignment, rhythm
    layout   -> propose many candidate layouts from a few templates
    critic   -> score every candidate against a rubric-shaped critic,
                re-rank the best ones by rendering them and checking pixels
    emit     -> write the winning plan back into the source schema and
                repair anything that would break a hard rule
"""
