(() => {
    "use strict";

    const explorer = document.querySelector("[data-candidate-explorer]");
    if (!explorer) {
        return;
    }

    const form = explorer.querySelector("[data-filter-form]");
    const rowsContainer = explorer.querySelector("[data-candidate-rows]");
    const rows = Array.from(explorer.querySelectorAll("[data-candidate-row]"));
    const emptyState = explorer.querySelector("[data-filter-empty]");
    const resultSummary = explorer.querySelector("[data-result-summary]");
    const actionFeedback = explorer.querySelector("[data-action-feedback]");
    const selectedCount = explorer.querySelector("[data-selected-count]");
    const selectedSize = explorer.querySelector("[data-selected-size]");
    const visibleSelection = explorer.querySelector("[data-visible-selection]");
    const selectVisibleButton = explorer.querySelector("[data-select-visible]");
    const clearSelectionButton = explorer.querySelector("[data-clear-selection]");
    const exportButton = explorer.querySelector("[data-export-selection]");
    const previousButton = explorer.querySelector("[data-page-previous]");
    const nextButton = explorer.querySelector("[data-page-next]");
    const pageStatus = explorer.querySelector("[data-page-status]");
    const pageSize = Number(explorer.dataset.pageSize) || 25;
    const selectedIds = new Set();
    let filteredRows = [];
    let visibleRows = [];
    let currentPage = 1;

    const confidenceOrder = {high: 0, medium: 1, low: 2};

    function formatBytes(value) {
        const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
        let amount = Number(value) || 0;
        let unitIndex = 0;
        while (Math.abs(amount) >= 1024 && unitIndex < units.length - 1) {
            amount /= 1024;
            unitIndex += 1;
        }
        if (unitIndex === 0) {
            return `${Math.trunc(amount).toLocaleString()} ${units[unitIndex]}`;
        }
        return `${amount.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        })} ${units[unitIndex]}`;
    }

    function selectedAttributes() {
        return Array.from(
            form.querySelectorAll('input[name="attribute"]:checked')
        ).map((input) => input.value);
    }

    function normalizedExtension(value) {
        const trimmed = value.trim().toLowerCase();
        if (!trimmed) {
            return "";
        }
        return trimmed.startsWith(".") ? trimmed : `.${trimmed}`;
    }

    function rowMatches(row) {
        const attributes = new Set(
            row.dataset.attributes.split(",").filter(Boolean)
        );
        const requestedAttributes = selectedAttributes();
        const matchMode = form.elements["match-mode"].value;
        const matchesAttributes =
            requestedAttributes.length === 0 ||
            (matchMode === "all"
                ? requestedAttributes.every((value) => attributes.has(value))
                : requestedAttributes.some((value) => attributes.has(value)));
        if (!matchesAttributes) {
            return false;
        }

        if (
            !form.elements["include-excluded"].checked &&
            row.dataset.eligibility !== "eligible"
        ) {
            return false;
        }

        const minimumSizeMiB = Number(form.elements["minimum-size"].value || 0);
        if (Number(row.dataset.sizeBytes) < minimumSizeMiB * 1024 * 1024) {
            return false;
        }

        const minimumAge = Number(form.elements["minimum-age"].value || 0);
        const ageDays = Number(row.dataset.ageDays);
        if (
            minimumAge > 0 &&
            (row.dataset.ageDays === "" || !Number.isFinite(ageDays) || ageDays < minimumAge)
        ) {
            return false;
        }

        const extension = normalizedExtension(form.elements.extension.value);
        if (extension && row.dataset.extension !== extension) {
            return false;
        }

        const pathQuery = form.elements["path-query"].value.trim().toLowerCase();
        if (pathQuery && !row.dataset.pathSort.includes(pathQuery)) {
            return false;
        }

        const root = form.elements["scan-root"].value;
        if (root && row.dataset.root !== root) {
            return false;
        }

        const confidence = form.elements.confidence.value;
        return !confidence || row.dataset.confidence === confidence;
    }

    function compareRows(left, right) {
        const sort = form.elements.sort.value;
        let result = 0;
        if (sort === "largest") {
            result = Number(right.dataset.sizeBytes) - Number(left.dataset.sizeBytes);
        } else if (sort === "smallest") {
            result = Number(left.dataset.sizeBytes) - Number(right.dataset.sizeBytes);
        } else if (sort === "oldest" || sort === "newest") {
            const leftTime = Date.parse(left.dataset.modified);
            const rightTime = Date.parse(right.dataset.modified);
            const safeLeft = Number.isNaN(leftTime) ? Number.POSITIVE_INFINITY : leftTime;
            const safeRight = Number.isNaN(rightTime) ? Number.POSITIVE_INFINITY : rightTime;
            result = sort === "oldest" ? safeLeft - safeRight : safeRight - safeLeft;
        } else if (sort === "confidence") {
            result =
                confidenceOrder[left.dataset.confidence] -
                confidenceOrder[right.dataset.confidence];
        } else {
            result = left.dataset.pathSort.localeCompare(right.dataset.pathSort);
        }

        if (result !== 0) {
            return result;
        }
        const pathResult = left.dataset.pathSort.localeCompare(right.dataset.pathSort);
        if (pathResult !== 0) {
            return pathResult;
        }
        return left.dataset.candidateId.localeCompare(right.dataset.candidateId);
    }

    function updateSelectionSummary() {
        let totalBytes = 0;
        rows.forEach((row) => {
            const checkbox = row.querySelector("[data-candidate-select]");
            const selected = selectedIds.has(row.dataset.candidateId);
            checkbox.checked = selected;
            if (selected) {
                totalBytes += Number(row.dataset.sizeBytes);
            }
        });

        const visibleSelected = visibleRows.filter((row) =>
            selectedIds.has(row.dataset.candidateId)
        ).length;
        selectedCount.textContent = `${selectedIds.size} selected`;
        selectedSize.textContent = formatBytes(totalBytes);
        visibleSelection.textContent = `${visibleSelected} visible on this page`;
        clearSelectionButton.disabled = selectedIds.size === 0;
        exportButton.disabled = selectedIds.size === 0;
        selectVisibleButton.disabled = !visibleRows.some(
            (row) => !row.querySelector("[data-candidate-select]").disabled
        );
    }

    function renderPage() {
        const pageCount = Math.max(1, Math.ceil(filteredRows.length / pageSize));
        currentPage = Math.min(Math.max(1, currentPage), pageCount);
        const start = (currentPage - 1) * pageSize;
        const end = Math.min(start + pageSize, filteredRows.length);
        visibleRows = filteredRows.slice(start, end);
        const visibleSet = new Set(visibleRows);

        rows.forEach((row) => {
            row.hidden = !visibleSet.has(row);
        });
        emptyState.hidden = filteredRows.length !== 0;
        previousButton.disabled = currentPage <= 1;
        nextButton.disabled = currentPage >= pageCount;
        pageStatus.textContent = `Page ${currentPage} of ${pageCount}`;
        resultSummary.textContent =
            filteredRows.length === 0
                ? `0 of ${rows.length} retained candidates match.`
                : `Showing ${start + 1}–${end} of ${filteredRows.length} matching ` +
                  `candidate${filteredRows.length === 1 ? "" : "s"} ` +
                  `(${rows.length} retained total).`;
        updateSelectionSummary();
    }

    function applyFilters() {
        filteredRows = rows.filter(rowMatches).sort(compareRows);
        filteredRows.forEach((row) => rowsContainer.append(row));
        currentPage = 1;
        renderPage();
    }

    async function copyText(value) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(value);
            return;
        }
        const temporary = document.createElement("textarea");
        temporary.value = value;
        temporary.setAttribute("readonly", "");
        temporary.className = "clipboard-fallback";
        document.body.append(temporary);
        temporary.select();
        const copied = document.execCommand("copy");
        temporary.remove();
        if (!copied) {
            throw new Error("Copy was not available in this browser.");
        }
    }

    function setFeedback(message, isError = false) {
        actionFeedback.textContent = message;
        actionFeedback.classList.toggle("is-error", isError);
    }

    form.addEventListener("input", applyFilters);
    form.addEventListener("change", applyFilters);
    form.addEventListener("reset", () => {
        window.setTimeout(applyFilters, 0);
    });

    rows.forEach((row) => {
        const checkbox = row.querySelector("[data-candidate-select]");
        checkbox.addEventListener("change", () => {
            if (checkbox.checked && !checkbox.disabled) {
                selectedIds.add(row.dataset.candidateId);
            } else {
                selectedIds.delete(row.dataset.candidateId);
            }
            updateSelectionSummary();
        });

        row.querySelector("[data-copy-path]").addEventListener("click", async () => {
            try {
                await copyText(row.dataset.candidatePath);
                setFeedback(`Copied path for ${row.dataset.candidateName}.`);
            } catch (error) {
                setFeedback(error.message || "The path could not be copied.", true);
            }
        });

        const openButton = row.querySelector("[data-open-folder]");
        openButton.addEventListener("click", async () => {
            openButton.disabled = true;
            setFeedback(`Opening the folder containing ${row.dataset.candidateName}…`);
            const body = new URLSearchParams({
                candidate_id: row.dataset.candidateId,
                action_token: openButton.dataset.actionToken,
            });
            try {
                const response = await fetch(openButton.dataset.actionUrl, {
                    method: "POST",
                    headers: {"Content-Type": "application/x-www-form-urlencoded"},
                    body,
                    credentials: "same-origin",
                });
                const result = await response.json();
                setFeedback(result.message, !response.ok);
            } catch (_error) {
                setFeedback("The containing folder could not be opened.", true);
            } finally {
                openButton.disabled = false;
            }
        });
    });

    selectVisibleButton.addEventListener("click", () => {
        visibleRows.forEach((row) => {
            const checkbox = row.querySelector("[data-candidate-select]");
            if (!checkbox.disabled) {
                selectedIds.add(row.dataset.candidateId);
            }
        });
        updateSelectionSummary();
    });

    clearSelectionButton.addEventListener("click", () => {
        selectedIds.clear();
        updateSelectionSummary();
    });

    previousButton.addEventListener("click", () => {
        currentPage -= 1;
        renderPage();
    });

    nextButton.addEventListener("click", () => {
        currentPage += 1;
        renderPage();
    });

    exportButton.addEventListener("click", () => {
        const candidates = rows
            .filter((row) => selectedIds.has(row.dataset.candidateId))
            .sort((left, right) => left.dataset.pathSort.localeCompare(right.dataset.pathSort))
            .map((row) => ({
                candidate_id: row.dataset.candidateId,
                path: row.dataset.candidatePath,
                size_bytes: Number(row.dataset.sizeBytes),
                attributes: row.dataset.attributes.split(",").filter(Boolean),
                confidence: row.dataset.confidence,
            }));
        const plan = {
            schema_version: "1.0.0",
            plan_type: "storage_cleanup_review",
            created_at_utc: new Date().toISOString(),
            action: "review_only_no_files_modified",
            selected_count: candidates.length,
            selected_unique_bytes: candidates.reduce(
                (total, candidate) => total + candidate.size_bytes,
                0
            ),
            candidates,
        };
        const blob = new Blob([`${JSON.stringify(plan, null, 2)}\n`], {
            type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const download = document.createElement("a");
        download.href = url;
        download.download = "storage-cleanup-review.json";
        document.body.append(download);
        download.click();
        download.remove();
        URL.revokeObjectURL(url);
        setFeedback("Exported a local review-only cleanup plan. No files were changed.");
    });

    applyFilters();
})();
