// Global variables
let activeJobId = null;
let statusPollInterval = null;
let spendChartInstance = null;

// Initialize Page
document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initDragAndDrop();
    initEventListeners();
    fetchJobsList();
});

// 1. Theme Management (Dark Mode Default, Togglable to Light Mode)
function initTheme() {
    const savedTheme = localStorage.getItem("theme") || "dark";
    if (savedTheme === "light") {
        document.body.classList.add("light-theme");
        updateThemeUI("light");
    } else {
        document.body.classList.remove("light-theme");
        updateThemeUI("dark");
    }
}

function toggleTheme() {
    const isLight = document.body.classList.toggle("light-theme");
    const theme = isLight ? "light" : "dark";
    localStorage.setItem("theme", theme);
    updateThemeUI(theme);
}

function updateThemeUI(theme) {
    const icon = document.getElementById("theme-icon");
    const text = document.getElementById("theme-text");
    
    if (theme === "light") {
        icon.setAttribute("data-lucide", "sun");
        text.textContent = "Light Mode";
    } else {
        icon.setAttribute("data-lucide", "moon");
        text.textContent = "Dark Mode";
    }
    
    // Re-trigger lucide to update icons
    lucide.createIcons();
    
    // Redraw chart if exists (to update text colors if needed)
    if (spendChartInstance) {
        updateChartTheme();
    }
}

// 2. Drag & Drop CSV Uploader
function initDragAndDrop() {
    const dropZone = document.getElementById("drop-zone");
    const fileInput = document.getElementById("file-input");

    dropZone.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length) {
            updateDropZonePrompt(fileInput.files[0].name);
        }
    });

    ["dragover", "dragleave", "drop"].forEach(eventName => {
        dropZone.addEventListener(eventName, e => {
            e.preventDefault();
            e.stopPropagation();
        });
    });

    dropZone.addEventListener("dragover", () => dropZone.classList.add("drop-zone--over"));
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drop-zone--over"));
    
    dropZone.addEventListener("drop", (e) => {
        dropZone.classList.remove("drop-zone--over");
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            updateDropZonePrompt(e.dataTransfer.files[0].name);
        }
    });
}

function updateDropZonePrompt(filename) {
    const prompt = document.querySelector(".drop-zone__prompt");
    prompt.innerHTML = `<strong>Selected file:</strong> ${filename}`;
}

// 3. Event Listeners
function initEventListeners() {
    // Theme Toggle
    document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
    
    // Refresh Jobs List
    document.getElementById("refresh-jobs").addEventListener("click", () => fetchJobsList());
    
    // Status Filter Change
    document.getElementById("status-filter").addEventListener("change", () => fetchJobsList());

    // CSV Form Submit
    document.getElementById("upload-form").addEventListener("submit", handleCSVUpload);
}

// 4. File Upload Handler
async function handleCSVUpload(e) {
    e.preventDefault();
    const fileInput = document.getElementById("file-input");
    const statusBox = document.getElementById("upload-status");
    const uploadBtn = document.getElementById("upload-btn");
    
    if (!fileInput.files.length) return;
    
    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append("file", file);

    // Disable button during upload
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = `<span class="spinner-mini"></span> Enqueuing Job...`;
    
    try {
        const response = await fetch("/jobs/upload", {
            method: "POST",
            body: formData
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Upload failed");
        }
        
        const data = await response.json();
        
        // Show success alert
        statusBox.classList.remove("hidden");
        statusBox.innerHTML = `<i data-lucide="check-circle" class="btn-icon"></i> Job enqueued successfully! ID: ${data.job_id}`;
        lucide.createIcons();
        
        // Reset form
        document.getElementById("upload-form").reset();
        document.querySelector(".drop-zone__prompt").textContent = "Drag & drop transactions.csv here or click to browse";
        
        // Refresh job history list
        fetchJobsList();
        
        // Active this job in panel
        showLoadingState(data.job_id, file.name);
        startPollingJobStatus(data.job_id);
        
    } catch (error) {
        statusBox.classList.remove("hidden");
        statusBox.style.borderColor = "var(--danger-border)";
        statusBox.style.color = "var(--danger-color)";
        statusBox.style.backgroundColor = "var(--danger-bg)";
        statusBox.innerHTML = `<i data-lucide="alert-octagon" class="btn-icon"></i> ${error.message}`;
        lucide.createIcons();
    } finally {
        uploadBtn.disabled = false;
        uploadBtn.innerHTML = `<i data-lucide="play" class="btn-icon-left"></i> Start Processing Pipeline`;
        lucide.createIcons();
    }
}

// 5. Jobs List Fetcher
async function fetchJobsList() {
    const listContainer = document.getElementById("jobs-list");
    const statusFilter = document.getElementById("status-filter").value;
    
    let url = "/jobs";
    if (statusFilter) {
        url += `?status=${statusFilter}`;
    }
    
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error("Could not load jobs");
        
        const jobs = await response.json();
        
        if (jobs.length === 0) {
            listContainer.innerHTML = `<div class="empty-state"><p>No pipeline runs found.</p></div>`;
            return;
        }
        
        listContainer.innerHTML = "";
        jobs.forEach(job => {
            const dateStr = new Date(job.created_at).toLocaleString();
            const activeClass = job.id === activeJobId ? "active" : "";
            
            const jobEl = document.createElement("div");
            jobEl.className = `job-item ${activeClass}`;
            jobEl.innerHTML = `
                <div class="job-item-header">
                    <span class="job-filename" title="${job.filename}">${job.filename}</span>
                    <span class="badge badge-${job.status}">${job.status}</span>
                </div>
                <div class="job-meta">
                    <span>Rows: ${job.row_count_clean || job.row_count_raw || '-'}</span>
                    <span>${dateStr}</span>
                </div>
            `;
            
            jobEl.addEventListener("click", () => selectJob(job.id, job.filename, job.status));
            listContainer.appendChild(jobEl);
        });
        
    } catch (error) {
        listContainer.innerHTML = `<div class="empty-state"><p class="text-danger">Error: ${error.message}</p></div>`;
    }
}

// Select a job to view
function selectJob(jobId, filename, status) {
    // Highlight selected job in list
    document.querySelectorAll(".job-item").forEach(el => {
        const nameEl = el.querySelector(".job-filename");
        if (nameEl && nameEl.textContent === filename) {
            el.classList.add("active");
        } else {
            el.classList.remove("active");
        }
    });

    activeJobId = jobId;
    
    // Stop any existing pollers
    if (statusPollInterval) {
        clearInterval(statusPollInterval);
    }
    
    if (status === "pending" || status === "processing") {
        showLoadingState(jobId, filename);
        startPollingJobStatus(jobId);
    } else if (status === "completed") {
        showResultsDashboard(jobId);
    } else {
        showFailedState(jobId, filename);
    }
}

// 6. Loading and Failed UI states
function showLoadingState(jobId, filename) {
    document.getElementById("results-placeholder").classList.add("hidden");
    document.getElementById("results-dashboard").classList.add("hidden");
    
    const loadingCard = document.getElementById("results-loading");
    loadingCard.classList.remove("hidden");
    
    document.getElementById("loading-title").textContent = "Job Pipeline Active";
    document.getElementById("loading-desc").innerHTML = `Processing <strong>${filename}</strong> asynchronously... <br>Executing cleaning steps, checking outliers, and communicating with Gemini LLM.`;
    document.getElementById("loading-meta").textContent = `Job ID: ${jobId}`;
}

function showFailedState(jobId, filename) {
    document.getElementById("results-placeholder").classList.add("hidden");
    document.getElementById("results-dashboard").classList.add("hidden");
    document.getElementById("results-loading").classList.add("hidden");
    
    const placeholder = document.getElementById("results-placeholder");
    placeholder.classList.remove("hidden");
    
    const body = placeholder.querySelector(".card-body");
    body.innerHTML = `
        <i data-lucide="alert-octagon" class="large-icon text-danger animate-pulse"></i>
        <h3 class="text-danger">Job Pipeline Failed</h3>
        <p>An error occurred while executing the transaction processing pipeline for <strong>${filename}</strong>.</p>
        <button onclick="fetchFailedJobDetails('${jobId}')" class="btn btn-secondary btn-sm" style="margin-top:10px;">View Error Details</button>
    `;
    lucide.createIcons();
}

async function fetchFailedJobDetails(jobId) {
    try {
        const response = await fetch(`/jobs/${jobId}/status`);
        if (!response.ok) throw new Error();
        const data = await response.json();
        alert(`Pipeline Job Error Details:\n\nJob ID: ${jobId}\nStatus: ${data.status}\nError Message:\n${data.error_message || "No error details available."}`);
    } catch {
        alert("Failed to retrieve error logs.");
    }
}

// 7. Polling Mechanism
function startPollingJobStatus(jobId) {
    statusPollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/jobs/${jobId}/status`);
            if (!response.ok) throw new Error("Error polling status");
            
            const data = await response.json();
            
            if (data.status === "completed") {
                clearInterval(statusPollInterval);
                fetchJobsList(); // update history list
                showResultsDashboard(jobId);
            } else if (data.status === "failed") {
                clearInterval(statusPollInterval);
                fetchJobsList(); // update history list
                showFailedState(jobId, data.filename);
            }
            
        } catch (error) {
            console.error("Polling error:", error);
        }
    }, 2000); // Poll every 2 seconds
}

// 8. Results Retrieval & Dashboard Rendering
async function showResultsDashboard(jobId) {
    // Show dashboard frame, hide loading
    document.getElementById("results-placeholder").classList.add("hidden");
    document.getElementById("results-loading").classList.add("hidden");
    
    const dashboard = document.getElementById("results-dashboard");
    dashboard.classList.remove("hidden");
    
    try {
        // Fetch full results
        const statusRes = await fetch(`/jobs/${jobId}/status`);
        const resultsRes = await fetch(`/jobs/${jobId}/results`);
        
        if (!statusRes.ok || !resultsRes.ok) throw new Error("Could not load job results");
        
        const statusData = await statusRes.json();
        const resultsData = await resultsRes.json();
        
        // Render Meta Info & Badges
        document.getElementById("meta-filename").textContent = statusData.filename;
        document.getElementById("meta-filename").title = statusData.filename;
        document.getElementById("meta-rows").textContent = `${statusData.row_count_clean} / ${statusData.row_count_raw}`;
        
        const summary = resultsData.llm_summary;
        if (summary) {
            document.getElementById("meta-spend-inr").textContent = `₹${summary.total_spend_inr.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            document.getElementById("meta-spend-usd").textContent = `$${summary.total_spend_usd.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            
            // Risk Badge styling
            const riskBadge = document.getElementById("risk-badge");
            riskBadge.textContent = `${summary.risk_level} risk`;
            riskBadge.className = `badge badge-${summary.risk_level === 'high' ? 'failed' : summary.risk_level === 'medium' ? 'pending' : 'completed'}`;
            
            // Narrative spending summary
            document.getElementById("summary-narrative").textContent = `"${summary.narrative}"`;
            
            // Top Merchants tags
            const merchantsContainer = document.getElementById("top-merchants-list");
            merchantsContainer.innerHTML = "";
            summary.top_merchants.forEach(m => {
                const tag = document.createElement("div");
                tag.className = "merchant-tag";
                tag.innerHTML = `
                    <span class="merchant-tag-name">${m.merchant}</span>
                    <span class="merchant-tag-spend">${m.count} txns · ₹${m.spend.toLocaleString('en-IN', {maximumFractionDigits: 0})}</span>
                `;
                merchantsContainer.appendChild(tag);
            });
        }
        
        // Render Spend Charts
        renderSpendChart(resultsData.category_spend_breakdown);
        
        // Render Flagged Anomalies Table
        renderAnomaliesTable(resultsData.flagged_anomalies);
        
        // Render Cleaned Dataset Table
        renderCleanedTable(resultsData.cleaned_transactions);
        
    } catch (error) {
        console.error(error);
        dashboard.innerHTML = `<div class="centered"><i data-lucide="alert-octagon" class="large-icon text-danger"></i><h3>Error Loading Results</h3><p>${error.message}</p></div>`;
        lucide.createIcons();
    }
}

// 9. Chart.js Category Breakdown rendering
function renderSpendChart(breakdown) {
    const canvas = document.getElementById("spend-chart");
    
    // Destroy previous chart
    if (spendChartInstance) {
        spendChartInstance.destroy();
    }
    
    const categories = Object.keys(breakdown);
    const inrSpends = categories.map(cat => breakdown[cat].inr);
    const usdSpends = categories.map(cat => breakdown[cat].usd);
    
    // Check if there's only one currency in the dataset to hide the other bar
    const hasINR = inrSpends.some(s => s > 0);
    const hasUSD = usdSpends.some(s => s > 0);
    
    const datasets = [];
    if (hasINR) {
        datasets.push({
            label: 'Spend in INR (₹)',
            data: inrSpends,
            backgroundColor: 'rgba(139, 92, 246, 0.75)', // Violet HSL
            borderColor: 'rgba(139, 92, 246, 1)',
            borderWidth: 1,
            yAxisID: 'y_inr'
        });
    }
    if (hasUSD) {
        datasets.push({
            label: 'Spend in USD ($)',
            data: usdSpends,
            backgroundColor: 'rgba(16, 185, 129, 0.75)', // Teal HSL
            borderColor: 'rgba(16, 185, 129, 1)',
            borderWidth: 1,
            yAxisID: 'y_usd'
        });
    }
    
    // Text colors depending on theme
    const isLightTheme = document.body.classList.contains("light-theme");
    const textColor = isLightTheme ? "#4b5563" : "#9ca3af";
    const gridColor = isLightTheme ? "rgba(0, 0, 0, 0.05)" : "rgba(255, 255, 255, 0.05)";
    
    spendChartInstance = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: categories,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    stacked: false,
                    grid: { color: gridColor },
                    ticks: { color: textColor, font: { family: '"Times New Roman"', size: 12 } }
                },
                y_inr: {
                    type: 'linear',
                    display: hasINR,
                    position: 'left',
                    grid: { color: gridColor },
                    title: { display: true, text: 'INR (₹)', color: textColor, font: { family: '"Times New Roman"' } },
                    ticks: { color: textColor, font: { family: '"Times New Roman"' } }
                },
                y_usd: {
                    type: 'linear',
                    display: hasUSD,
                    position: 'right',
                    grid: { drawOnChartArea: false }, // avoid double grids
                    title: { display: true, text: 'USD ($)', color: textColor, font: { family: '"Times New Roman"' } },
                    ticks: { color: textColor, font: { family: '"Times New Roman"' } }
                }
            },
            plugins: {
                legend: {
                    labels: { color: textColor, font: { family: '"Times New Roman"', size: 12 } }
                },
                tooltip: {
                    titleFont: { family: '"Times New Roman"' },
                    bodyFont: { family: '"Times New Roman"' }
                }
            }
        }
    });
}

function updateChartTheme() {
    if (!spendChartInstance) return;
    const isLightTheme = document.body.classList.contains("light-theme");
    const textColor = isLightTheme ? "#4b5563" : "#9ca3af";
    const gridColor = isLightTheme ? "rgba(0, 0, 0, 0.05)" : "rgba(255, 255, 255, 0.05)";
    
    spendChartInstance.options.scales.x.ticks.color = textColor;
    spendChartInstance.options.scales.x.grid.color = gridColor;
    
    if (spendChartInstance.options.scales.y_inr) {
        spendChartInstance.options.scales.y_inr.ticks.color = textColor;
        spendChartInstance.options.scales.y_inr.grid.color = gridColor;
        spendChartInstance.options.scales.y_inr.title.color = textColor;
    }
    
    if (spendChartInstance.options.scales.y_usd) {
        spendChartInstance.options.scales.y_usd.ticks.color = textColor;
        spendChartInstance.options.scales.y_usd.title.color = textColor;
    }
    
    spendChartInstance.options.plugins.legend.labels.color = textColor;
    spendChartInstance.update();
}

// 10. Table Renders
function renderAnomaliesTable(anomalies) {
    const tableBody = document.querySelector("#anomalies-table tbody");
    const tableContainer = document.getElementById("anomalies-table");
    const emptyMsg = document.getElementById("no-anomalies-msg");
    const countBadge = document.getElementById("anomalies-count-badge");
    
    // Set badge text
    countBadge.textContent = `${anomalies.length} Flagged`;
    if (anomalies.length > 0) {
        countBadge.className = "badge badge-failed";
    } else {
        countBadge.className = "badge badge-completed";
    }
    
    if (anomalies.length === 0) {
        tableContainer.classList.add("hidden");
        emptyMsg.classList.remove("hidden");
        return;
    }
    
    tableContainer.classList.remove("hidden");
    emptyMsg.classList.add("hidden");
    
    tableBody.innerHTML = "";
    anomalies.forEach(an => {
        const row = document.createElement("tr");
        row.innerHTML = `
            <td><code>${an.account_id}</code></td>
            <td><strong>${an.merchant}</strong></td>
            <td class="text-danger">${an.amount.toLocaleString(an.currency === 'USD' ? 'en-US' : 'en-IN', {minimumFractionDigits: 2})}</td>
            <td><span class="badge ${an.currency === 'USD' ? 'badge-completed' : 'badge-processing'}">${an.currency}</span></td>
            <td><span class="text-danger" style="font-weight:600;"><i data-lucide="alert-triangle" class="btn-icon-left" style="width:1.1em;height:1.1em;vertical-align:middle;display:inline-block;"></i> ${an.anomaly_reason}</span></td>
        `;
        tableBody.appendChild(row);
    });
    
    lucide.createIcons();
}

function renderCleanedTable(transactions) {
    const tableBody = document.querySelector("#cleaned-table tbody");
    tableBody.innerHTML = "";
    
    transactions.forEach(tx => {
        const row = document.createElement("tr");
        
        // Category Badge
        const catClean = tx.category.toLowerCase().replace(" ", "-");
        let badgeStyle = "table-badge-other";
        if (catClean.includes("food")) badgeStyle = "table-badge-food";
        else if (catClean.includes("shop")) badgeStyle = "table-badge-shopping";
        else if (catClean.includes("travel")) badgeStyle = "table-badge-travel";
        else if (catClean.includes("transport")) badgeStyle = "table-badge-transport";
        else if (catClean.includes("utility")) badgeStyle = "table-badge-utilities";
        else if (catClean.includes("cash") || catClean.includes("withdraw")) badgeStyle = "table-badge-cash";
        else if (catClean.includes("ent")) badgeStyle = "table-badge-ent";
        
        // Source description
        let sourceHtml = `<span class="badge badge-completed">Original</span>`;
        if (tx.llm_category) {
            if (tx.llm_failed) {
                sourceHtml = `<span class="badge badge-failed" title="${tx.llm_raw_response}">LLM Failed</span>`;
            } else {
                sourceHtml = `<span class="badge badge-processing" title="Gemini 1.5 Classified">LLM enriched</span>`;
            }
        }
        
        const isAnomalyClass = tx.is_anomaly ? 'style="background: rgba(239,68,68,0.04);"' : '';
        
        row.innerHTML = `
            <td ${isAnomalyClass}><code>${tx.txn_id || '-'}</code></td>
            <td ${isAnomalyClass}>${tx.date}</td>
            <td ${isAnomalyClass}><strong>${tx.merchant}</strong></td>
            <td ${isAnomalyClass} style="font-weight:600;">${tx.amount.toLocaleString(tx.currency === 'USD' ? 'en-US' : 'en-IN', {minimumFractionDigits: 2})}</td>
            <td ${isAnomalyClass}><span class="badge ${tx.currency === 'USD' ? 'badge-completed' : 'badge-processing'}">${tx.currency}</span></td>
            <td ${isAnomalyClass}><span class="badge ${tx.status === 'SUCCESS' ? 'badge-completed' : tx.status === 'PENDING' ? 'badge-pending' : 'badge-failed'}">${tx.status}</span></td>
            <td ${isAnomalyClass}><code>${tx.account_id}</code></td>
            <td ${isAnomalyClass}><span class="table-badge ${badgeStyle}">${tx.category}</span></td>
            <td ${isAnomalyClass}>${sourceHtml}</td>
        `;
        tableBody.appendChild(row);
    });
}
