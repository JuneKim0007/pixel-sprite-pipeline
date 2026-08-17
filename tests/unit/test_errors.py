from __future__ import annotations

import pytest

from pipeline.generation.comfy import ComfyError
from pipeline.generation.runner import PipelineError
from pipeline.looks.styles import StyleError
from pipeline.orchestration.queue import QueueError
from pipeline.shared import PixelError, body_for, errors, status_for
from pipeline.shared.files import PathDenied

OWN = [(errors.NotFound("style sheet", "x"), 404),
       (errors.Invalid("bad"), 400),
       (errors.Denied("no"), 403),
       (errors.Conflict("busy"), 409),
       (errors.Unavailable("down"), 503),
       (errors.Internal("boom"), 500)]

# A service being down is not a defect in this code, which is the judgement
# the queue also makes when it pauses instead of failing jobs.
DOMAIN = [(ComfyError("down"), 503),
          (StyleError("cycle"), 400),
          (QueueError("bad"), 400),
          (PipelineError("order"), 400),
          (PathDenied("outside"), 403)]

# Builtins are translated while raise sites are still being named.
BUILTIN = [(ValueError("x"), 400), (FileNotFoundError("x"), 404),
           (RuntimeError("x"), 500)]


@pytest.mark.parametrize("exc,want", OWN + DOMAIN + BUILTIN,
                         ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else v)
def test_failure_carries_its_own_status(exc, want):
    assert status_for(exc) == want


@pytest.mark.parametrize("exc,_want", DOMAIN, ids=lambda v: type(v).__name__)
def test_domain_errors_stay_inside_the_taxonomy(exc, _want):
    assert isinstance(exc, PixelError)


def test_an_unnamed_failure_shows_its_type():
    assert body_for(RuntimeError("x"))["error"].startswith("RuntimeError:")


def test_not_found_reports_the_alternatives():
    listed = errors.NotFound("style sheet", "nope", available=["a", "b"])
    assert "a, b" in listed.as_dict().get("hint", "")


@pytest.mark.parametrize("exc,status", [
    (TimeoutError, 504),
    (PermissionError, 403),
    (NotADirectoryError, 400),
    (IsADirectoryError, 400),
])
def test_library_raised_failures_keep_their_status(exc, status):
    """These have no raise site in this codebase and never did.

    They map what stdlib raises on a request path - urllib's TimeoutError from
    the ComfyUI client, os's permission and directory errors. A tidy-up that
    removes an entry because nothing here raises it turns a 504 into a 500,
    which is the bug the taxonomy exists to prevent.
    """
    assert errors.BUILTIN_STATUS[exc] == status
    assert errors.status_for(exc("x")) == status
