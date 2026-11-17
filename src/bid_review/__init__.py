"""Evidence-first public SI bid review pipeline."""

__all__ = ["BidReviewCrew"]


def __getattr__(name):
    if name == "BidReviewCrew":
        from .crew import BidReviewCrew
        return BidReviewCrew
    raise AttributeError(name)
