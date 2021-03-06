# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2020 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

import pytest

import hypothesis.internal.conjecture.floats as flt
from hypothesis.internal.compat import int_to_bytes
from hypothesis.internal.conjecture.engine import ConjectureRunner
from hypothesis.internal.conjecture.shrinker import Shrinker, block_program
from hypothesis.internal.conjecture.shrinking import Float
from hypothesis.internal.conjecture.utils import Sampler
from tests.conjecture.common import SOME_LABEL, run_to_buffer, shrinking_from


@pytest.mark.parametrize("n", [1, 5, 8, 15])
def test_can_shrink_variable_draws_with_just_deletion(n, monkeypatch):
    @shrinking_from([n] + [0] * (n - 1) + [1])
    def shrinker(data):
        n = data.draw_bits(4)
        b = [data.draw_bits(8) for _ in range(n)]
        if any(b):
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["minimize_individual_blocks"])

    assert list(shrinker.shrink_target.buffer) == [1, 1]


def test_deletion_and_lowering_fails_to_shrink(monkeypatch):
    monkeypatch.setattr(
        Shrinker,
        "shrink",
        lambda self: self.fixate_shrink_passes(["minimize_individual_blocks"]),
    )

    def gen(self):
        self.cached_test_function(10)

    monkeypatch.setattr(ConjectureRunner, "generate_new_examples", gen)

    @run_to_buffer
    def x(data):
        for _ in range(10):
            data.draw_bytes(1)
        data.mark_interesting()

    assert x == bytes(10)


def test_duplicate_blocks_that_go_away():
    @shrinking_from([1, 1, 1, 2] * 2 + [5] * 2)
    def shrinker(data):
        x = data.draw_bits(32)
        y = data.draw_bits(32)
        if x != y:
            data.mark_invalid()
        b = [data.draw_bytes(1) for _ in range(x & 255)]
        if len(set(b)) <= 1:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["minimize_duplicated_blocks"])
    assert shrinker.shrink_target.buffer == bytes(8)


def test_accidental_duplication(monkeypatch):
    @shrinking_from([18] * 20)
    def shrinker(data):
        x = data.draw_bits(8)
        y = data.draw_bits(8)
        if x != y:
            data.mark_invalid()
        if x < 5:
            data.mark_invalid()
        b = [data.draw_bytes(1) for _ in range(x)]
        if len(set(b)) == 1:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["minimize_duplicated_blocks"])
    assert list(shrinker.buffer) == [5] * 7


def test_can_zero_subintervals(monkeypatch):
    @shrinking_from(bytes([3, 0, 0, 0, 1]) * 10)
    def shrinker(data):
        for _ in range(10):
            data.start_example(SOME_LABEL)
            n = data.draw_bits(8)
            data.draw_bytes(n)
            data.stop_example()
            if data.draw_bits(8) != 1:
                return
        data.mark_interesting()

    shrinker.fixate_shrink_passes(["zero_examples"])
    assert list(shrinker.buffer) == [0, 1] * 10


def test_can_pass_to_an_indirect_descendant(monkeypatch):
    def tree(data):
        data.start_example(1)
        n = data.draw_bits(1)
        label = data.draw_bits(8)
        if n:
            tree(data)
            tree(data)
        data.stop_example(1)
        return label

    initial = bytes([1, 10, 0, 0, 1, 0, 0, 10, 0, 0])
    target = bytes([0, 10])

    good = {initial, target}

    @shrinking_from(initial)
    def shrinker(data):
        tree(data)
        if bytes(data.buffer) in good:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["pass_to_descendant"])

    assert shrinker.shrink_target.buffer == target


def shrink(buffer, *passes):
    def accept(f):
        shrinker = shrinking_from(buffer)(f)

        shrinker.fixate_shrink_passes(passes)

        return list(shrinker.buffer)

    return accept


def test_shrinking_blocks_from_common_offset():
    @shrinking_from([11, 10])
    def shrinker(data):
        m = data.draw_bits(8)
        n = data.draw_bits(8)
        if abs(m - n) <= 1 and max(m, n) > 0:
            data.mark_interesting()

    shrinker.mark_changed(0)
    shrinker.mark_changed(1)
    shrinker.lower_common_block_offset()

    x = shrinker.shrink_target.buffer

    assert sorted(x) == [0, 1]


def test_handle_empty_draws(monkeypatch):
    monkeypatch.setattr(
        Shrinker,
        "shrink",
        lambda self: self.fixate_shrink_passes(["adaptive_example_deletion"]),
    )

    @run_to_buffer
    def x(data):
        while True:
            data.start_example(SOME_LABEL)
            n = data.draw_bits(1)
            data.start_example(SOME_LABEL)
            data.stop_example()
            data.stop_example(discard=n > 0)
            if not n:
                break
        data.mark_interesting()

    assert x == bytes([0])


def test_can_reorder_examples():
    @shrinking_from([1, 0, 1, 1, 0, 1, 0, 0, 0])
    def shrinker(data):
        total = 0
        for _ in range(5):
            data.start_example(0)
            if data.draw_bits(8):
                total += data.draw_bits(9)
            data.stop_example(0)
        if total == 2:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["reorder_examples"])

    assert list(shrinker.buffer) == [0, 0, 0, 1, 0, 1, 1, 0, 1]


def test_permits_but_ignores_raising_order(monkeypatch):
    monkeypatch.setattr(
        ConjectureRunner,
        "generate_new_examples",
        lambda runner: runner.cached_test_function([1]),
    )

    monkeypatch.setattr(
        Shrinker, "shrink", lambda self: self.incorporate_new_buffer(bytes([2]))
    )

    @run_to_buffer
    def x(data):
        data.draw_bits(2)
        data.mark_interesting()

    assert list(x) == [1]


def test_block_deletion_can_delete_short_ranges(monkeypatch):
    @shrinking_from([v for i in range(5) for _ in range(i + 1) for v in [0, i]])
    def shrinker(data):
        while True:
            n = data.draw_bits(16)
            for _ in range(n):
                if data.draw_bits(16) != n:
                    data.mark_invalid()
            if n == 4:
                data.mark_interesting()

    shrinker.fixate_shrink_passes([block_program("X" * i) for i in range(1, 5)])
    assert list(shrinker.shrink_target.buffer) == [0, 4] * 5


def test_try_shrinking_blocks_ignores_overrun_blocks(monkeypatch):
    monkeypatch.setattr(
        ConjectureRunner,
        "generate_new_examples",
        lambda runner: runner.cached_test_function([3, 3, 0, 1]),
    )

    monkeypatch.setattr(
        Shrinker,
        "shrink",
        lambda self: self.try_shrinking_blocks((0, 1, 5), bytes([2])),
    )

    @run_to_buffer
    def x(data):
        n1 = data.draw_bits(8)
        data.draw_bits(8)
        if n1 == 3:
            data.draw_bits(8)
        k = data.draw_bits(8)
        if k == 1:
            data.mark_interesting()

    assert list(x) == [2, 2, 1]


def test_dependent_block_pairs_is_up_to_shrinking_integers():
    # Unit test extracted from a failure in tests/nocover/test_integers.py
    distribution = Sampler([4.0, 8.0, 1.0, 1.0, 0.5])

    sizes = [8, 16, 32, 64, 128]

    @shrinking_from(b"\x03\x01\x00\x00\x00\x00\x00\x01\x00\x02\x01")
    def shrinker(data):
        size = sizes[distribution.sample(data)]
        result = data.draw_bits(size)
        sign = (-1) ** (result & 1)
        result = (result >> 1) * sign
        cap = data.draw_bits(8)

        if result >= 32768 and cap == 1:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["minimize_individual_blocks"])
    assert list(shrinker.shrink_target.buffer) == [1, 1, 0, 1, 0, 0, 1]


def test_finding_a_minimal_balanced_binary_tree():
    # Tests iteration while the shape of the thing being iterated over can
    # change. In particular the current example can go from trivial to non
    # trivial.

    def tree(data):
        # Returns height of a binary tree and whether it is height balanced.
        data.start_example("tree")
        n = data.draw_bits(1)
        if n == 0:
            result = (1, True)
        else:
            h1, b1 = tree(data)
            h2, b2 = tree(data)
            result = (1 + max(h1, h2), b1 and b2 and abs(h1 - h2) <= 1)
        data.stop_example("tree")
        return result

    # Starting from an unbalanced tree of depth six
    @shrinking_from([1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
    def shrinker(data):
        _, b = tree(data)
        if not b:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["adaptive_example_deletion", "reorder_examples"])

    assert list(shrinker.shrink_target.buffer) == [1, 0, 1, 0, 1, 0, 0]


def test_alphabet_minimization():
    @shrink(bytes((10, 11)) * 5, "alphabet_minimize")
    def x(data):
        buf = data.draw_bytes(10)
        if len(set(buf)) > 2:
            data.mark_invalid()
        if buf[0] < buf[1] and buf[1] > 1:
            data.mark_interesting()

    assert x == [0, 2] * 5


def test_float_shrink_can_run_when_canonicalisation_does_not_work(monkeypatch):
    # This should be an error when called
    monkeypatch.setattr(Float, "shrink", None)

    base_buf = int_to_bytes(flt.base_float_to_lex(1000.0), 8) + bytes(1)

    @shrinking_from(base_buf)
    def shrinker(data):
        flt.draw_float(data)
        if bytes(data.buffer) == base_buf:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["minimize_floats"])

    assert shrinker.shrink_target.buffer == base_buf


def test_try_shrinking_blocks_out_of_bounds():
    @shrinking_from(bytes([1]))
    def shrinker(data):
        data.draw_bits(1)
        data.mark_interesting()

    assert not shrinker.try_shrinking_blocks((1,), bytes([1]))


def test_block_programs_are_adaptive():
    @shrinking_from(bytes(1000) + bytes([1]))
    def shrinker(data):
        while not data.draw_bits(1):
            pass
        data.mark_interesting()

    p = shrinker.add_new_pass(block_program("X"))
    shrinker.fixate_shrink_passes([p.name])

    assert len(shrinker.shrink_target.buffer) == 1
    assert shrinker.calls <= 60


def test_zero_examples_is_adaptive():
    @shrinking_from(bytes([1]) * 1001)
    def shrinker(data):
        for _ in range(1000):
            data.draw_bits(1)
        if data.draw_bits(1):
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["zero_examples"])

    assert shrinker.shrink_target.buffer == bytes(1000) + bytes([1])
    assert shrinker.calls <= 60


def test_zero_examples_with_variable_min_size():
    @shrinking_from(bytes([255]) * 100)
    def shrinker(data):
        any_nonzero = False
        for i in range(1, 10):
            any_nonzero |= data.draw_bits(i * 8) > 0
        if not any_nonzero:
            data.mark_invalid()
        data.mark_interesting()

    shrinker.fixate_shrink_passes(["zero_examples"])
    assert len([d for d in shrinker.shrink_target.blocks if not d.all_zero]) == 1


def test_zero_contained_examples():
    @shrinking_from(bytes([1]) * 8)
    def shrinker(data):
        for _ in range(4):
            data.start_example(1)
            if data.draw_bits(8) == 0:
                data.mark_invalid()
            data.start_example(1)
            data.draw_bits(8)
            data.stop_example()
            data.stop_example()
        data.mark_interesting()

    shrinker.fixate_shrink_passes(["zero_examples"])
    assert list(shrinker.shrink_target.buffer) == [1, 0] * 4


def test_adaptive_example_deletion_deletes_nothing():
    @shrinking_from(bytes([1]) * 8 + bytes(1))
    def shrinker(data):
        n = 0
        while data.draw_bits(8):
            n += 1
        if n >= 8:
            data.mark_interesting()

    shrinker.fixate_shrink_passes(["adaptive_example_deletion"])
    assert list(shrinker.shrink_target.buffer) == [1] * 8 + [0]


def test_zig_zags_quickly():
    @shrinking_from(bytes([255]) * 4)
    def shrinker(data):
        m = data.draw_bits(16)
        n = data.draw_bits(16)
        if m == 0 or n == 0:
            data.mark_invalid()
        if abs(m - n) <= 1:
            data.mark_interesting(0)
        # Two different interesting origins for avoiding slipping in the
        # shrinker.
        if abs(m - n) <= 10:
            data.mark_interesting(1)

    shrinker.fixate_shrink_passes(["minimize_individual_blocks"])
    assert shrinker.engine.valid_examples <= 100
    assert list(shrinker.shrink_target.buffer) == [0, 1, 0, 1]
