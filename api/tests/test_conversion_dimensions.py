from src.calculation.conversion.dimensions import DimensionVector


def test_dimension_vectors_compare_by_exponents():
    mass = DimensionVector({"mass": 1})
    emissions = DimensionVector({"co2e": 1})

    assert mass != emissions
    assert mass.is_compatible_with(DimensionVector({"mass": 1}))
    assert not mass.is_compatible_with(emissions)


def test_dimension_vectors_support_compound_units():
    emissions_intensity = DimensionVector({"co2e": 1, "energy": -1})
    same = DimensionVector({"energy": -1, "co2e": 1})
    multiplied = DimensionVector({"co2e": 1}) / DimensionVector({"energy": 1})

    assert emissions_intensity == same
    assert multiplied == same


def test_dimension_vectors_normalize_zeroes_and_sort_output():
    vector = DimensionVector({"time": 0, "energy": -1, "co2e": 1})

    assert vector.as_dict() == {"co2e": 1, "energy": -1}


def test_dimension_vector_exponents_are_publicly_immutable():
    vector = DimensionVector({"mass": 1})

    try:
        vector.exponents["mass"] = 2
    except TypeError:
        pass
    else:
        raise AssertionError("dimension exponents mapping was mutable")


def test_dimension_vectors_support_multiply_divide_and_power():
    length = DimensionVector({"length": 1})
    time = DimensionVector({"time": 1})

    speed = length / time
    area = length**2
    acceleration = speed / time

    assert speed == DimensionVector({"length": 1, "time": -1})
    assert area == DimensionVector({"length": 2})
    assert acceleration == DimensionVector({"length": 1, "time": -2})


def test_currency_dimension_is_only_expression_compatibility():
    eur = DimensionVector({"currency": 1})
    usd = DimensionVector({"currency": 1})

    assert eur.is_compatible_with(usd)
    assert (eur / usd).as_dict() == {}


def test_dimension_vector_rejects_unknown_dimension_key():
    try:
        DimensionVector({"unknown": 1})
    except ValueError as exc:
        assert "unknown dimension" in str(exc)
    else:
        raise AssertionError("unknown dimension key was accepted")
