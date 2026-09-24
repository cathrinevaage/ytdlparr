"""SAB's category model: a "*" block others inherit from, named
blocks overriding it, and the job spec overriding those. Three
layers, resolved into one set of options per job."""

SPEC_KEYS = ("format", "container", "subs", "embed", "sidecar", "sponsorblock")


def resolve(categories, category, spec):
    """The options one job runs with."""
    base = dict(categories.get("*", {}))
    named = categories.get(category, {}) if category else {}
    from_spec = {
        key: spec[key] for key in SPEC_KEYS if spec.get(key) is not None
    }

    return {**base, **named, **from_spec}


def advertised(categories):
    """Everything except the "*" block, which SAB also lists but which
    Sonarr should not be offered as a choice."""
    return [name for name in categories if name != "*"]
