(() => {
    "use strict";

    const explorer = document.querySelector("[data-file-type-explorer]");
    if (!explorer) {
        return;
    }

    const childrenUrl = explorer.dataset.childrenUrl;
    const filesUrl = explorer.dataset.filesUrl;
    const tree = explorer.querySelector("[data-folder-tree]");
    const treeStatus = explorer.querySelector("[data-tree-status]");
    const breadcrumbs = explorer.querySelector("[data-folder-breadcrumbs]");
    const scopeList = explorer.querySelector("[data-selected-scope-list]");
    const scopeCount = explorer.querySelector("[data-selected-scope-count]");
    const scopeFeedback = explorer.querySelector("[data-scope-feedback]");
    const emptyScopeMessage = explorer.querySelector("[data-empty-scope-message]");
    const sizeHeading = explorer.querySelector("[data-size-heading]");
    const activeFilterLabel = explorer.querySelector("[data-active-filter]");
    const selectedScopes = new Map();
    const selectedFiles = new Map();
    const pendingNameClicks = new WeakMap();
    let requestReviewRefresh = () => {};
    let clearSelectedFilesForScopeChange = () => {};

    const formatBytes = (value) => {
        const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
        let amount = Number(value) || 0;
        let unitIndex = 0;
        while (Math.abs(amount) >= 1024 && unitIndex < units.length - 1) {
            amount /= 1024;
            unitIndex += 1;
        }
        if (unitIndex === 0) {
            return `${Math.trunc(amount).toLocaleString()} B`;
        }
        return `${amount.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        })} ${units[unitIndex]}`;
    };

    const parseExtensionBytes = (node) => {
        try {
            return JSON.parse(node.dataset.extensionBytes || "{}");
        } catch (_error) {
            return {};
        }
    };

    const extensionGroups = () => Array.from(
        explorer.querySelectorAll("[data-extension-group]")
    );

    const activeExtensions = () => {
        const extensions = new Set();
        extensionGroups().forEach((group) => {
            const groupToggle = group.querySelector("[data-group-toggle]");
            if (!groupToggle.checked) {
                return;
            }
            group.querySelectorAll("[data-extension-toggle]:checked").forEach(
                (toggle) => extensions.add(toggle.value.toLowerCase())
            );
        });
        return extensions;
    };

    const activeGroupLabels = () => extensionGroups()
        .filter((group) => group.querySelector("[data-group-toggle]").checked)
        .map((group) => group.querySelector(".extension-group-toggle span").textContent.trim());

    const bytesForRecord = (record, extensions) => {
        if (extensions.size === 0) {
            return Number(record.totalBytes) || 0;
        }
        return Array.from(extensions).reduce(
            (total, extension) => total + Number(record.extensionBytes[extension] || 0),
            0
        );
    };

    const recordFromNode = (node) => ({
        folderId: node.dataset.folderId,
        path: node.dataset.folderPath,
        name: node.dataset.folderName,
        totalBytes: Number(node.dataset.totalBytes) || 0,
        extensionBytes: parseExtensionBytes(node),
        accessState: node.dataset.accessState,
        accessLabel: node.dataset.accessLabel,
        accessExplanation: node.dataset.accessExplanation,
    });

    const normalizedPath = (value) => value
        .replaceAll("/", "\\")
        .replace(/\\+$/, "")
        .toLowerCase();

    const pathContains = (parent, child) => {
        const normalizedParent = normalizedPath(parent);
        const normalizedChild = normalizedPath(child);
        return normalizedChild === normalizedParent
            || normalizedChild.startsWith(`${normalizedParent}\\`);
    };

    const scopesOverlap = (left, right) => (
        pathContains(left, right) || pathContains(right, left)
    );

    const setScopeFeedback = (message, isError = false) => {
        scopeFeedback.textContent = message;
        scopeFeedback.classList.toggle("is-error", isError);
    };

    const updateScopeCheckboxes = () => {
        explorer.querySelectorAll("[data-tree-node]").forEach((node) => {
            const checkbox = node.querySelector(":scope > .folder-tree-row [data-folder-scope]");
            if (checkbox) {
                checkbox.checked = selectedScopes.has(node.dataset.folderId);
            }
        });
    };

    const renderSelectedScopes = () => {
        const extensions = activeExtensions();
        scopeList.querySelectorAll("[data-selected-scope]").forEach((item) => item.remove());
        emptyScopeMessage.hidden = selectedScopes.size > 0;
        scopeCount.textContent = String(selectedScopes.size);

        Array.from(selectedScopes.values())
            .sort((left, right) => left.path.localeCompare(right.path, undefined, {
                numeric: true,
                sensitivity: "base",
            }))
            .forEach((record) => {
                const item = document.createElement("li");
                item.dataset.selectedScope = record.folderId;
                item.className = "selected-scope-item";

                const copy = document.createElement("div");
                const name = document.createElement("strong");
                name.textContent = record.name;
                const path = document.createElement("span");
                path.className = "secondary-text";
                path.textContent = record.path;
                const status = document.createElement("span");
                status.className = `scope-access scope-access-${record.accessState}`;
                status.textContent = record.accessLabel;
                copy.append(name, path, status);

                const value = document.createElement("strong");
                value.className = "selected-scope-size";
                value.textContent = formatBytes(bytesForRecord(record, extensions));

                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "text-button";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", `Remove ${record.path} from review scopes`);
                remove.addEventListener("click", () => {
                    selectedScopes.delete(record.folderId);
                    updateScopeCheckboxes();
                    renderSelectedScopes();
                    setScopeFeedback(`${record.path} was removed.`);
                    clearSelectedFilesForScopeChange();
                    requestReviewRefresh(true);
                });
                item.append(copy, value, remove);
                scopeList.append(item);
            });
    };

    const selectNode = (node, shouldSelect) => {
        const record = recordFromNode(node);
        if (!shouldSelect) {
            selectedScopes.delete(record.folderId);
            updateScopeCheckboxes();
            renderSelectedScopes();
            setScopeFeedback(`${record.path} was removed.`);
            clearSelectedFilesForScopeChange();
            requestReviewRefresh(true);
            return true;
        }
        if (node.dataset.scopeSelectable !== "true") {
            setScopeFeedback(
                `${record.path} is unavailable and cannot be selected as a review scope.`,
                true
            );
            updateScopeCheckboxes();
            return false;
        }
        const overlap = Array.from(selectedScopes.values()).find(
            (selected) => selected.folderId !== record.folderId
                && scopesOverlap(selected.path, record.path)
        );
        if (overlap) {
            setScopeFeedback(
                `Choose either ${overlap.path} or ${record.path}; parent and child scopes cannot overlap.`,
                true
            );
            updateScopeCheckboxes();
            return false;
        }
        selectedScopes.set(record.folderId, record);
        updateScopeCheckboxes();
        renderSelectedScopes();
        setScopeFeedback(`${record.path} was added for later file review.`);
        clearSelectedFilesForScopeChange();
        requestReviewRefresh(true);
        return true;
    };

    const setBreadcrumb = (path) => {
        breadcrumbs.replaceChildren();
        const parts = path.replace(/\\+$/, "").split("\\").filter(Boolean);
        parts.forEach((part, index) => {
            if (index > 0) {
                const separator = document.createElement("span");
                separator.setAttribute("aria-hidden", "true");
                separator.textContent = "›";
                breadcrumbs.append(separator);
            }
            const crumb = document.createElement("span");
            crumb.textContent = index === 0 ? `${part}\\` : part;
            breadcrumbs.append(crumb);
        });
    };

    const childContainer = (node) => Array.from(node.children).find(
        (child) => child.matches("[data-tree-children]")
    );

    const updateNodeValue = (node, extensions) => {
        const value = bytesForRecord(recordFromNode(node), extensions);
        node.dataset.displayBytes = String(value);
        const label = node.querySelector(":scope > .folder-tree-row [data-folder-size]");
        if (label) {
            label.textContent = formatBytes(value);
        }
    };

    const sortChildList = (list) => {
        const nodes = Array.from(list.children).filter(
            (child) => child.matches("[data-tree-node]")
        );
        nodes.sort((left, right) => {
            const sizeDifference = Number(right.dataset.displayBytes || 0)
                - Number(left.dataset.displayBytes || 0);
            if (sizeDifference !== 0) {
                return sizeDifference;
            }
            return left.dataset.folderName.localeCompare(
                right.dataset.folderName,
                undefined,
                { numeric: true, sensitivity: "base" }
            );
        });
        nodes.forEach((node) => list.append(node));
    };

    const updateFilterPresentation = () => {
        const extensions = activeExtensions();
        const labels = activeGroupLabels();
        explorer.querySelectorAll("[data-tree-node]").forEach(
            (node) => updateNodeValue(node, extensions)
        );
        explorer.querySelectorAll("[data-tree-children]").forEach(sortChildList);

        if (extensions.size === 0) {
            sizeHeading.textContent = "All files total";
            activeFilterLabel.textContent = "Showing total logical folder size";
        } else {
            sizeHeading.textContent = `${labels.join(" + ")} total`;
            activeFilterLabel.textContent = `Matching ${Array.from(extensions).join(", ")}`;
        }
        renderSelectedScopes();
        requestReviewRefresh(true);
    };

    const statusClass = (accessState) => {
        if (accessState === "normal") {
            return "healthy";
        }
        if (accessState === "review_only") {
            return "warning";
        }
        return "unavailable";
    };

    const createTreeNode = (folder) => {
        const node = document.createElement("li");
        node.setAttribute("role", "treeitem");
        node.setAttribute("aria-level", String(folder.depth + 1));
        node.dataset.treeNode = "";
        node.dataset.folderId = folder.folder_id;
        node.dataset.parentId = folder.parent_id || "";
        node.dataset.folderPath = folder.path;
        node.dataset.folderName = folder.name;
        node.dataset.totalBytes = String(folder.total_bytes);
        node.dataset.extensionBytes = JSON.stringify(folder.extension_bytes);
        node.dataset.accessState = folder.access_state;
        node.dataset.accessLabel = folder.access_label;
        node.dataset.accessExplanation = folder.access_explanation;
        node.dataset.scopeSelectable = String(folder.scope_selectable);
        node.dataset.hasChildren = String(folder.has_children);
        node.dataset.displayBytes = String(folder.total_bytes);

        const row = document.createElement("div");
        row.className = "folder-tree-row";

        const expand = document.createElement("button");
        expand.type = "button";
        expand.className = "tree-expand-button";
        expand.dataset.treeExpand = "";
        expand.textContent = folder.has_children ? "▸" : "";
        expand.disabled = !folder.has_children;
        expand.setAttribute("aria-label", `Expand ${folder.name}`);

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.dataset.folderScope = "";
        checkbox.disabled = !folder.scope_selectable;
        checkbox.setAttribute("aria-label", `Select ${folder.path} as a review scope`);

        const name = document.createElement("button");
        name.type = "button";
        name.className = "folder-name-button";
        name.dataset.folderNameButton = "";
        name.textContent = folder.name;

        const access = document.createElement("span");
        access.className = `status-badge status-${statusClass(folder.access_state)} folder-access`;
        access.textContent = folder.access_label;
        access.title = folder.access_explanation;

        const size = document.createElement("strong");
        size.className = "folder-tree-size";
        size.dataset.folderSize = "";
        size.textContent = formatBytes(folder.total_bytes);
        row.append(expand, checkbox, name, access, size);
        node.append(row);

        const children = document.createElement("ul");
        children.setAttribute("role", "group");
        children.dataset.treeChildren = "";
        children.hidden = true;
        node.append(children);
        if (folder.has_children) {
            node.setAttribute("aria-expanded", "false");
        }
        bindTreeNode(node);
        return node;
    };

    const loadChildren = async (node) => {
        const container = childContainer(node);
        if (node.dataset.loaded === "true") {
            return container;
        }
        node.setAttribute("aria-busy", "true");
        treeStatus.textContent = `Loading ${node.dataset.folderPath}…`;
        try {
            const url = new URL(childrenUrl, window.location.origin);
            url.searchParams.set("parent_id", node.dataset.folderId);
            const response = await fetch(url, {
                method: "GET",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.message || "Folder details could not be loaded.");
            }
            payload.children.forEach((folder) => container.append(createTreeNode(folder)));
            node.dataset.loaded = "true";
            updateFilterPresentation();
            treeStatus.textContent = `${payload.children.length} child folder(s) loaded.`;
            return container;
        } catch (error) {
            treeStatus.textContent = error instanceof Error
                ? error.message
                : "Folder details could not be loaded.";
            treeStatus.classList.add("is-error");
            return null;
        } finally {
            node.removeAttribute("aria-busy");
        }
    };

    const setExpanded = async (node, shouldExpand) => {
        if (node.dataset.hasChildren !== "true") {
            return;
        }
        const container = childContainer(node);
        const expand = node.querySelector(":scope > .folder-tree-row [data-tree-expand]");
        if (shouldExpand) {
            const loadedContainer = await loadChildren(node);
            if (!loadedContainer) {
                return;
            }
            loadedContainer.hidden = false;
            node.setAttribute("aria-expanded", "true");
            expand.textContent = "▾";
            expand.setAttribute("aria-label", `Collapse ${node.dataset.folderName}`);
        } else {
            container.hidden = true;
            node.setAttribute("aria-expanded", "false");
            expand.textContent = "▸";
            expand.setAttribute("aria-label", `Expand ${node.dataset.folderName}`);
        }
        setBreadcrumb(node.dataset.folderPath);
    };

    const toggleExpanded = (node) => setExpanded(
        node,
        node.getAttribute("aria-expanded") !== "true"
    );

    const visibleNameButtons = () => Array.from(
        tree.querySelectorAll("[data-folder-name-button]")
    ).filter((button) => button.offsetParent !== null);

    function bindTreeNode(node) {
        const expand = node.querySelector(":scope > .folder-tree-row [data-tree-expand]");
        const checkbox = node.querySelector(":scope > .folder-tree-row [data-folder-scope]");
        const name = node.querySelector(":scope > .folder-tree-row [data-folder-name-button]");

        expand.addEventListener("click", () => toggleExpanded(node));
        checkbox.addEventListener("change", () => selectNode(node, checkbox.checked));
        name.addEventListener("click", () => {
            const pending = window.setTimeout(() => {
                pendingNameClicks.delete(name);
                setBreadcrumb(node.dataset.folderPath);
                if (!checkbox.disabled) {
                    selectNode(node, !selectedScopes.has(node.dataset.folderId));
                } else {
                    setScopeFeedback(node.dataset.accessExplanation, true);
                }
            }, 220);
            pendingNameClicks.set(name, pending);
        });
        name.addEventListener("dblclick", (event) => {
            event.preventDefault();
            const pending = pendingNameClicks.get(name);
            if (pending) {
                window.clearTimeout(pending);
                pendingNameClicks.delete(name);
            }
            toggleExpanded(node);
        });
        name.addEventListener("keydown", (event) => {
            if (event.key === "ArrowRight") {
                event.preventDefault();
                setExpanded(node, true);
            } else if (event.key === "ArrowLeft") {
                event.preventDefault();
                setExpanded(node, false);
            } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const buttons = visibleNameButtons();
                const current = buttons.indexOf(name);
                const direction = event.key === "ArrowDown" ? 1 : -1;
                const target = buttons[current + direction];
                if (target) {
                    target.focus();
                }
            }
        });
    }

    const reviewSection = explorer.querySelector("[data-file-review]");
    const reviewForm = explorer.querySelector("[data-file-review-form]");
    const reviewMessage = explorer.querySelector("[data-file-review-message]");
    const reviewResults = explorer.querySelector("[data-file-review-results]");
    const reviewBody = explorer.querySelector("[data-file-review-body]");
    const matchingCount = explorer.querySelector("[data-matching-count]");
    const matchingSize = explorer.querySelector("[data-matching-size]");
    const selectedFileCount = explorer.querySelector("[data-selected-file-count]");
    const selectedFileSize = explorer.querySelector("[data-selected-file-size]");
    const selectedFileList = explorer.querySelector("[data-selected-file-list]");
    const noSelectedFiles = explorer.querySelector("[data-no-selected-files]");
    const selectVisibleButton = explorer.querySelector("[data-select-visible]");
    const clearFileSelectionButton = explorer.querySelector("[data-clear-file-selection]");
    const previousPageButton = explorer.querySelector("[data-previous-page]");
    const nextPageButton = explorer.querySelector("[data-next-page]");
    const pageSummary = explorer.querySelector("[data-page-summary]");
    const boundedDetailWarning = explorer.querySelector("[data-bounded-detail-warning]");
    let currentReviewPage = 1;
    let currentPageFiles = [];
    let lastSelectedVisibleIndex = null;
    let reviewRequestNumber = 0;
    let reviewTimer = null;

    const updateSelectedFileSummary = () => {
        const files = Array.from(selectedFiles.values()).sort(
            (left, right) => left.path.localeCompare(right.path, undefined, {
                numeric: true,
                sensitivity: "base",
            })
        );
        const bytes = files.reduce((total, file) => total + file.size_bytes, 0);
        selectedFileCount.textContent = `${files.length.toLocaleString()} selected`;
        selectedFileSize.textContent = `${formatBytes(bytes)} logical selected`;
        clearFileSelectionButton.disabled = files.length === 0;
        noSelectedFiles.hidden = files.length > 0;
        selectedFileList.querySelectorAll("[data-selected-file]").forEach(
            (item) => item.remove()
        );
        files.forEach((file) => {
            const item = document.createElement("li");
            item.dataset.selectedFile = file.file_id;
            const copy = document.createElement("div");
            const name = document.createElement("strong");
            name.textContent = file.name;
            const path = document.createElement("span");
            path.className = "secondary-text";
            path.textContent = file.path;
            copy.append(name, path);
            const size = document.createElement("strong");
            size.textContent = file.size;
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "text-button";
            remove.textContent = "Remove";
            remove.setAttribute("aria-label", `Remove ${file.path} from selected files`);
            remove.addEventListener("click", () => {
                selectedFiles.delete(file.file_id);
                updateSelectedFileSummary();
                renderFileRows(currentPageFiles);
            });
            item.append(copy, size, remove);
            selectedFileList.append(item);
        });
    };

    const setFileSelected = (file, selected) => {
        if (!file.selectable) {
            return;
        }
        if (selected) {
            selectedFiles.set(file.file_id, file);
        } else {
            selectedFiles.delete(file.file_id);
        }
    };

    clearSelectedFilesForScopeChange = () => {
        if (selectedFiles.size === 0) {
            return;
        }
        selectedFiles.clear();
        updateSelectedFileSummary();
    };

    const formatModified = (value) => {
        if (!value) {
            return "Unavailable";
        }
        const parsed = new Date(value);
        return Number.isNaN(parsed.valueOf()) ? "Unavailable" : parsed.toLocaleString();
    };

    const createFileRow = (file, index) => {
        const row = document.createElement("tr");
        row.dataset.fileId = file.file_id;
        const selectCell = document.createElement("td");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.dataset.fileSelect = "";
        checkbox.disabled = !file.selectable;
        checkbox.checked = selectedFiles.has(file.file_id);
        checkbox.setAttribute("aria-label", `Select ${file.path}`);
        checkbox.addEventListener("click", (event) => {
            if (
                event.shiftKey
                && lastSelectedVisibleIndex !== null
                && lastSelectedVisibleIndex !== index
            ) {
                const first = Math.min(lastSelectedVisibleIndex, index);
                const last = Math.max(lastSelectedVisibleIndex, index);
                currentPageFiles.slice(first, last + 1).forEach(
                    (rangeFile) => setFileSelected(rangeFile, checkbox.checked)
                );
                renderFileRows(currentPageFiles);
            } else {
                setFileSelected(file, checkbox.checked);
            }
            lastSelectedVisibleIndex = index;
            updateSelectedFileSummary();
        });
        selectCell.append(checkbox);

        const fileCell = document.createElement("td");
        const name = document.createElement("strong");
        name.textContent = file.name;
        const path = document.createElement("span");
        path.className = "secondary-text file-path";
        path.textContent = file.path;
        fileCell.append(name, path);

        const sizeCell = document.createElement("td");
        sizeCell.textContent = file.size;
        const modifiedCell = document.createElement("td");
        modifiedCell.textContent = formatModified(file.modified_at_utc);
        const extensionCell = document.createElement("td");
        extensionCell.textContent = file.extension;

        const stateCell = document.createElement("td");
        const badge = document.createElement("span");
        const badgeClass = file.selectable
            ? "healthy"
            : file.selection_state === "review_only"
                ? "warning"
                : "unavailable";
        badge.className = `status-badge status-${badgeClass}`;
        badge.textContent = file.selection_label;
        const reason = document.createElement("span");
        reason.className = "secondary-text";
        reason.textContent = file.protection_reason;
        stateCell.append(badge, reason);
        row.append(selectCell, fileCell, sizeCell, modifiedCell, extensionCell, stateCell);
        return row;
    };

    function renderFileRows(files) {
        reviewBody.replaceChildren();
        files.forEach((file, index) => reviewBody.append(createFileRow(file, index)));
        selectVisibleButton.disabled = !files.some((file) => file.selectable);
    }

    const resetReviewDisplay = (message) => {
        currentPageFiles = [];
        lastSelectedVisibleIndex = null;
        reviewBody.replaceChildren();
        reviewResults.hidden = true;
        matchingCount.textContent = "0 matching files";
        matchingSize.textContent = "0 B in retained index details";
        selectVisibleButton.disabled = true;
        previousPageButton.disabled = true;
        nextPageButton.disabled = true;
        boundedDetailWarning.hidden = true;
        reviewMessage.hidden = false;
        reviewMessage.textContent = message;
        reviewMessage.classList.remove("is-error");
    };

    const buildReviewQuery = () => {
        const extensions = Array.from(activeExtensions());
        if (extensions.length === 0) {
            return {ready: false, message: "Choose at least one file-type group to review matching files."};
        }
        if (selectedScopes.size === 0) {
            return {ready: false, message: "Choose at least one folder review scope to review matching files."};
        }
        const formData = new FormData(reviewForm);
        const minimumSize = Number(formData.get("minimum_size_mib"));
        const minimumAge = formData.get("minimum_age_days");
        const parameters = new URLSearchParams();
        selectedScopes.forEach((scope) => parameters.append("folder_id", scope.folderId));
        extensions.forEach((extension) => parameters.append("extension", extension));
        parameters.set("scope_mode", String(formData.get("scope_mode") || "recursive"));
        parameters.set("filename", String(formData.get("filename") || ""));
        parameters.set("sort", String(formData.get("sort") || "largest"));
        parameters.set("page", String(currentReviewPage));
        parameters.set("page_size", "25");
        if (minimumSize > 0) {
            parameters.set("minimum_size_bytes", String(Math.ceil(minimumSize * 1024 * 1024)));
        }
        if (minimumAge !== "") {
            parameters.set("minimum_age_days", String(minimumAge));
        }
        return {ready: true, parameters};
    };

    const loadMatchingFiles = async () => {
        const query = buildReviewQuery();
        if (!query.ready) {
            resetReviewDisplay(query.message);
            return;
        }
        const requestNumber = ++reviewRequestNumber;
        reviewMessage.hidden = false;
        reviewMessage.classList.remove("is-error");
        reviewMessage.textContent = "Loading retained matching-file details…";
        try {
            const url = new URL(filesUrl, window.location.origin);
            url.search = query.parameters.toString();
            const response = await fetch(url, {
                method: "GET",
                credentials: "same-origin",
                headers: {Accept: "application/json"},
            });
            const payload = await response.json();
            if (requestNumber !== reviewRequestNumber) {
                return;
            }
            if (!response.ok || !payload.ok) {
                throw new Error(payload.message || "Matching files could not be loaded.");
            }
            currentReviewPage = payload.page;
            currentPageFiles = payload.files;
            lastSelectedVisibleIndex = null;
            renderFileRows(currentPageFiles);
            matchingCount.textContent = `${payload.matching_count.toLocaleString()} matching files`;
            matchingSize.textContent = `${payload.matching_size} in retained index details`;
            pageSummary.textContent = `Page ${payload.page} of ${payload.total_pages} · rows ${payload.first_row}–${payload.last_row}`;
            previousPageButton.disabled = payload.page <= 1;
            nextPageButton.disabled = payload.page >= payload.total_pages;
            reviewResults.hidden = false;
            reviewMessage.hidden = payload.matching_count > 0;
            reviewMessage.textContent = payload.matching_count > 0
                ? ""
                : "No retained files match the selected scopes and filters.";
            boundedDetailWarning.hidden = payload.detail_coverage !== "bounded";
            if (payload.detail_coverage === "bounded") {
                boundedDetailWarning.textContent = `${payload.omitted_files.toLocaleString()} indexed file rows (${payload.omitted_size}) were omitted by the index detail limit. Folder aggregates remain truthful, but omitted paths cannot be selected until a less-bounded index is generated.`;
            }
        } catch (error) {
            if (requestNumber !== reviewRequestNumber) {
                return;
            }
            resetReviewDisplay(
                error instanceof Error ? error.message : "Matching files could not be loaded."
            );
            reviewMessage.classList.add("is-error");
        }
    };

    requestReviewRefresh = (resetPage = false) => {
        if (resetPage) {
            currentReviewPage = 1;
        }
        if (reviewTimer !== null) {
            window.clearTimeout(reviewTimer);
        }
        reviewTimer = window.setTimeout(loadMatchingFiles, 180);
    };

    reviewForm.addEventListener("input", () => requestReviewRefresh(true));
    reviewForm.addEventListener("change", (event) => {
        if (event.target.name === "scope_mode") {
            clearSelectedFilesForScopeChange();
        }
        requestReviewRefresh(true);
    });
    reviewForm.addEventListener("reset", () => {
        window.setTimeout(() => requestReviewRefresh(true), 0);
    });
    previousPageButton.addEventListener("click", () => {
        currentReviewPage = Math.max(1, currentReviewPage - 1);
        requestReviewRefresh(false);
    });
    nextPageButton.addEventListener("click", () => {
        currentReviewPage += 1;
        requestReviewRefresh(false);
    });
    selectVisibleButton.addEventListener("click", () => {
        currentPageFiles.forEach((file) => setFileSelected(file, true));
        renderFileRows(currentPageFiles);
        updateSelectedFileSummary();
    });
    clearFileSelectionButton.addEventListener("click", () => {
        selectedFiles.clear();
        renderFileRows(currentPageFiles);
        updateSelectedFileSummary();
    });

    extensionGroups().forEach((group) => {
        const groupToggle = group.querySelector("[data-group-toggle]");
        const extensionToggles = Array.from(
            group.querySelectorAll("[data-extension-toggle]")
        );
        const settingsButton = group.querySelector("[data-extension-settings-button]");
        const settings = group.querySelector("[data-extension-settings]");

        const updateGroupState = () => {
            const checkedCount = extensionToggles.filter((toggle) => toggle.checked).length;
            if (groupToggle.checked && checkedCount === 0) {
                groupToggle.checked = false;
            }
            groupToggle.indeterminate = groupToggle.checked
                && checkedCount > 0
                && checkedCount < extensionToggles.length;
            group.classList.toggle("is-active", groupToggle.checked);
            group.classList.toggle(
                "is-partial",
                groupToggle.checked && checkedCount < extensionToggles.length
            );
        };

        groupToggle.addEventListener("change", () => {
            if (groupToggle.checked && !extensionToggles.some((toggle) => toggle.checked)) {
                extensionToggles.forEach((toggle) => { toggle.checked = true; });
            }
            updateGroupState();
            updateFilterPresentation();
        });
        extensionToggles.forEach((toggle) => toggle.addEventListener("change", () => {
            updateGroupState();
            updateFilterPresentation();
        }));
        settingsButton.addEventListener("click", () => {
            const expanded = settingsButton.getAttribute("aria-expanded") === "true";
            settingsButton.setAttribute("aria-expanded", String(!expanded));
            settings.hidden = expanded;
        });
        updateGroupState();
    });

    explorer.querySelector("[data-clear-type-filters]").addEventListener("click", () => {
        extensionGroups().forEach((group) => {
            const groupToggle = group.querySelector("[data-group-toggle]");
            groupToggle.checked = false;
            groupToggle.indeterminate = false;
            group.querySelectorAll("[data-extension-toggle]").forEach(
                (toggle) => { toggle.checked = true; }
            );
            group.classList.remove("is-active", "is-partial");
        });
        updateFilterPresentation();
    });

    const rootNode = explorer.querySelector("[data-tree-node]");
    bindTreeNode(rootNode);
    updateSelectedFileSummary();
    updateFilterPresentation();
    setBreadcrumb(rootNode.dataset.folderPath);
    setExpanded(rootNode, true);
})();
