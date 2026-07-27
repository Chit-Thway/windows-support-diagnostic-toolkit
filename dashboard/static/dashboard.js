(() => {
    "use strict";

    const filterButtons = document.querySelectorAll("[data-status-filter]");
    const checks = document.querySelectorAll("[data-check-status]");
    const emptyMessage = document.querySelector(".js-filter-empty");

    filterButtons.forEach((button) => {
        button.addEventListener("click", () => {
            const selectedStatus = button.dataset.statusFilter;
            let visibleCount = 0;

            filterButtons.forEach((candidate) => {
                const isActive = candidate === button;
                candidate.classList.toggle("is-active", isActive);
                candidate.setAttribute("aria-pressed", String(isActive));
            });

            checks.forEach((check) => {
                const isVisible =
                    selectedStatus === "all" ||
                    check.dataset.checkStatus === selectedStatus;
                check.hidden = !isVisible;
                if (isVisible) {
                    visibleCount += 1;
                }
            });

            if (emptyMessage) {
                emptyMessage.hidden = visibleCount !== 0;
            }
        });
    });

    document.querySelectorAll(".js-local-time").forEach((timeElement) => {
        const value = timeElement.getAttribute("datetime");
        const parsed = value ? new Date(value) : null;
        if (parsed && !Number.isNaN(parsed.getTime())) {
            timeElement.textContent = parsed.toLocaleString();
            timeElement.title = `UTC: ${value}`;
        }
    });
})();
