"""Purged chronological ML experiments. No arbitrary model execution or broker access."""

import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss, mean_squared_error
from sklearn.inspection import permutation_importance
from sklearn.calibration import calibration_curve
from aegis.domain import stable_id


class CalibratedBaseline:
    def __init__(self, model="logistic", calibration="platt", seed=7):
        self.model_name, self.calibration, self.seed = model, calibration, seed
        if calibration not in {"platt", "isotonic"}:
            raise ValueError("Use platt or isotonic calibration")
        estimators = {
            "logistic": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, random_state=seed)),
            "random_forest": RandomForestClassifier(
                n_estimators=100, max_depth=4, min_samples_leaf=10, random_state=seed, n_jobs=1
            ),
            "gradient_boosting": HistGradientBoostingClassifier(
                max_iter=100, max_leaf_nodes=7, random_state=seed
            ),
        }
        self.model = estimators[model]
        self.calibrator = None

    def fit(self, x_train, y_train, x_validation, y_validation):
        if len(np.unique(y_train)) < 2 or len(np.unique(y_validation)) < 2:
            raise ValueError("Both outcome classes required in train and calibration periods")
        self.model.fit(x_train, y_train)
        p = self.model.predict_proba(x_validation)[:, 1]
        if self.calibration == "platt":
            self.calibrator = LogisticRegression().fit(self._logits(p), y_validation)
        else:
            self.calibrator = IsotonicRegression(out_of_bounds="clip").fit(p, y_validation)
        return self

    @staticmethod
    def _logits(p):
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def predict(self, x):
        p = self.model.predict_proba(x)[:, 1]
        return (
            self.calibrator.predict_proba(self._logits(p))[:, 1]
            if self.calibration == "platt"
            else self.calibrator.predict(p)
        )


def probability_report(y, p, bins=10):
    y, p = np.asarray(y), np.asarray(p)
    observed, predicted = calibration_curve(y, p, n_bins=bins, strategy="uniform")
    ece = 0
    histogram = []
    for i in range(bins):
        mask = (p >= i / bins) & (p < (i + 1) / bins if i < bins - 1 else p <= 1)
        n = int(mask.sum())
        if n:
            ece += n / len(p) * abs(float(p[mask].mean() - y[mask].mean()))
        histogram.append({"lower": i / bins, "upper": (i + 1) / bins, "count": n})
    return {
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, np.c_[1 - p, p], labels=[0, 1])),
        "expected_calibration_error": float(ece),
        "reliability_curve": {"predicted": predicted.tolist(), "observed": observed.tolist()},
        "prediction_histogram": histogram,
        "samples": len(y),
    }


def purged_split(rows, train_fraction=0.6, validation_fraction=0.2, embargo_seconds=300):
    from datetime import datetime, timedelta

    rows = sorted(rows, key=lambda r: r["timestamp"])
    if (
        len(rows) < 50
        or train_fraction <= 0
        or validation_fraction <= 0
        or train_fraction + validation_fraction >= 1
    ):
        raise ValueError("At least 50 rows and valid temporal proportions required")
    a, b = int(len(rows) * train_fraction), int(len(rows) * (train_fraction + validation_fraction))
    va, ta = datetime.fromisoformat(rows[a]["timestamp"]), datetime.fromisoformat(rows[b]["timestamp"])
    train = [
        r
        for r in rows[:a]
        if datetime.fromisoformat(r["label_end"]) < va - timedelta(seconds=embargo_seconds)
    ]
    valid = [
        r
        for r in rows[a:b]
        if datetime.fromisoformat(r["label_end"]) < ta - timedelta(seconds=embargo_seconds)
    ]
    test = rows[b:]
    if min(len(train), len(valid), len(test)) < 5:
        raise ValueError("Too few rows after purging overlapping labels")
    return train, valid, test


def experiment(rows, feature_names, model="logistic", calibration="platt", seed=7, ablation=True):
    train, valid, test = purged_split(rows)

    def arrays(part, names):
        return np.array([[r["features"][n] for n in names] for r in part], dtype=float), np.array(
            [r["target"] for r in part]
        )

    xt, yt = arrays(train, feature_names)
    xv, yv = arrays(valid, feature_names)
    xs, ys = arrays(test, feature_names)
    if not all(np.isfinite(x).all() for x in (xt, xv, xs)):
        raise ValueError("Invalid feature values")
    fitted = CalibratedBaseline(model, calibration, seed).fit(xt, yt, xv, yv)
    probabilities = fitted.predict(xs)
    result = {
        "model": model,
        "calibration": calibration,
        "seed": seed,
        "features": feature_names,
        "train_samples": len(train),
        "validation_samples": len(valid),
        "test_samples": len(test),
        "test_period": [test[0]["timestamp"], test[-1]["timestamp"]],
        "metrics": probability_report(ys, probabilities),
        "constant_baseline": probability_report(ys, np.repeat(yt.mean(), len(ys))),
    }

    def calibrated_score(estimator, x, y):
        return -brier_score_loss(y, estimator.predict(x))

    importance = permutation_importance(
        fitted, xs, ys, scoring=calibrated_score, n_repeats=5, random_state=seed
    )
    result["permutation_importance"] = dict(zip(feature_names, importance.importances_mean.tolist()))
    result["ablation"] = {}
    if ablation and len(feature_names) > 1:
        for name in feature_names:
            names = [n for n in feature_names if n != name]
            ax, ay = arrays(train, names)
            bx, by = arrays(valid, names)
            cx, cy = arrays(test, names)
            alternative = CalibratedBaseline(model, calibration, seed).fit(ax, ay, bx, by)
            result["ablation"][name] = probability_report(cy, alternative.predict(cx))
    # Regression is compared to a train-only mean target, never a full-period target estimate.
    if all("future_return" in r for r in rows):
        regression = make_pipeline(StandardScaler(), Ridge()).fit(xt, [r["future_return"] for r in train])
        actual = np.array([r["future_return"] for r in test])
        predicted = regression.predict(xs)
        result["regression"] = {
            "mse": float(mean_squared_error(actual, predicted)),
            "constant_mse": float(
                mean_squared_error(actual, np.repeat(np.mean([r["future_return"] for r in train]), len(test)))
            ),
        }
        result["ranked_predictions"] = sorted(
            [
                {
                    "timestamp": r["timestamp"],
                    "symbol": r.get("symbol", "unknown"),
                    "expected_return": float(p),
                }
                for r, p in zip(test, predicted)
            ],
            key=lambda r: -r["expected_return"],
        )
    result["id"] = stable_id(
        model, calibration, seed, feature_names, result["test_period"], result["metrics"]
    )
    result["limitations"] = [
        "Test ablations are diagnostics, not a license to retune on test",
        "No automatic promotion or online retraining",
    ]
    return fitted, result


def drift_report(reference, current, bins=10, threshold=0.25):
    reports = {}
    for name in reference:
        baseline = np.array(reference[name], dtype=float)
        recent = np.array(current.get(name, []), dtype=float)
        if (
            len(baseline) < 20
            or len(recent) < 20
            or not np.isfinite(baseline).all()
            or not np.isfinite(recent).all()
        ):
            reports[name] = {"status": "INSUFFICIENT_OR_INVALID_DATA", "blocked": True}
            continue
        cuts = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
        if len(cuts) < 3:
            changed = float(np.mean(np.abs(recent - float(baseline.mean())) > 1e-8))
            reports[name] = {"shift_fraction": changed, "blocked": changed > 0.2}
            continue
        cuts[0], cuts[-1] = -np.inf, np.inf
        p, q = np.histogram(baseline, cuts)[0] / len(baseline), np.histogram(recent, cuts)[0] / len(recent)
        p, q = np.clip(p, 1e-5, None), np.clip(q, 1e-5, None)
        psi = float(np.sum((q - p) * np.log(q / p)))
        reports[name] = {"psi": psi, "blocked": psi > threshold}
    return {
        "features": reports,
        "blocked": any(r["blocked"] for r in reports.values()),
        "threshold": threshold,
    }
