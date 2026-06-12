// dashboard.js — CareerMyntra Frontend JS
// Connected to Flask backend at localhost:5000
const BACKEND = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? "http://127.0.0.1:5000"
  : "https://careermyntra-portal-6.onrender.com";

const courses = [
  { name:"11th FYJC",        sub:"Maharashtra",      icon:"🏫", color:"#4f46e5" },
  { name:"Engineering",      sub:"MHT-CET / JEE",    icon:"⚙️", color:"#1565c0" },
  { name:"Architecture",     sub:"JEE P2 / NATA",    icon:"🏛️", color:"#0891b2" },
  { name:"Design",           sub:"CET / UCEED",      icon:"🎨", color:"#ea580c" },
  { name:"Pharmacy",         sub:"MHT-CET GPAT",     icon:"💊", color:"#e11d48" },
  { name:"Medical",          sub:"NEET",              icon:"🩺", color:"#7c3aed" },
  { name:"Nursing",          sub:"CET / NEET",        icon:"🩹", color:"#0d9488" },
  { name:"Animal & Fishery", sub:"MHT-CET / NEET",   icon:"🐟", color:"#0369a1" },
  { name:"Agriculture",      sub:"MHT-CET",           icon:"🌾", color:"#16a34a" },
  { name:"Law",              sub:"CET / CLAT",        icon:"⚖️", color:"#475569" },
  { name:"Management",       sub:"MHT-CET CAT",      icon:"💼", color:"#d97706" },
  { name:"Hotel Management", sub:"CET / NCHM JEE",   icon:"🏨", color:"#9333ea" },
];

let selectedCourse = null;
let selectedFile   = null;

function hexToRgba(hex, a) {
  const r=parseInt(hex.slice(1,3),16), g=parseInt(hex.slice(3,5),16), b=parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${a})`;
}

// ── Render course cards ──
function renderCourses() {
  const grid = document.getElementById("course-grid");
  grid.innerHTML = courses.map(c => `
    <div class="course-card" style="--card-color:${c.color}; --card-bg:${hexToRgba(c.color,0.1)}">
      <div class="card-top">
        <div class="card-icon">${c.icon}</div>
        <div>
          <div class="card-title">${c.name}</div>
          <div class="card-sub">${c.sub}</div>
        </div>
      </div>
      <button class="upload-btn" onclick="openModal('${c.name}','${c.sub}','${c.icon}')">
        ⬆️ Upload PDF / Excel
      </button>
    </div>
  `).join("");
}

// ── Tab switch ──
function switchTab(el) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  el.classList.add("active");
}

// ── Modal open/close ──
function openModal(name, sub, icon) {
  selectedCourse = { name, sub, icon };
  selectedFile   = null;
  document.getElementById("modal-icon").textContent    = icon;
  document.getElementById("modal-title").textContent   = name;
  document.getElementById("modal-sub").textContent     = sub;
  document.getElementById("file-preview").textContent  = "";
  document.getElementById("btn-start").disabled        = true;
  document.getElementById("file-input").value          = "";
  document.getElementById("drop-zone").style.borderColor = "#c7d7f5";
  document.getElementById("upload-error").style.display = "none";
  document.getElementById("modal").classList.add("open");
}

function closeModal() {
  document.getElementById("modal").classList.remove("open");
}

function closeOutside(e) {
  if (e.target.id === "modal") closeModal();
}

// ── File selected ──
function fileSelected(input) {
  if (input.files[0]) {
    selectedFile = input.files[0];
    document.getElementById("file-preview").textContent      = "📎 " + selectedFile.name;
    document.getElementById("btn-start").disabled            = false;
    document.getElementById("drop-zone").style.borderColor   = "#16a34a";
  }
}

// ── Drag & Drop ──
function dragOver(e) {
  e.preventDefault();
  document.getElementById("drop-zone").classList.add("dragover");
}

function dragLeave() {
  document.getElementById("drop-zone").classList.remove("dragover");
}

function dropFile(e) {
  e.preventDefault();
  document.getElementById("drop-zone").classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (![".pdf", ".xls", ".xlsx"].includes(ext)) {
    showUploadError("❌ Only PDF, XLS, XLSX files allowed!");
    return;
  }
  selectedFile = file;
  document.getElementById("file-preview").textContent    = "📎 " + file.name;
  document.getElementById("btn-start").disabled          = false;
  document.getElementById("drop-zone").style.borderColor = "#16a34a";
}

function showUploadError(msg) {
  const el = document.getElementById("upload-error");
  el.textContent    = msg;
  el.style.display  = "block";
}

// ── MAIN: Upload to backend & go to progress ──
async function goToProgress() {
  if (!selectedCourse || !selectedFile) return;

  const btn = document.getElementById("btn-start");
  btn.disabled     = true;
  btn.textContent  = "⏳ Uploading...";

  try {
    // Build FormData
    const formData = new FormData();
    formData.append("file",        selectedFile);
    formData.append("course_name", selectedCourse.name);

    // POST /api/upload
    const res  = await fetch(`${BACKEND}/api/upload`, {
      method: "POST",
      body:   formData
    });

    const data = await res.json();

    if (data.success) {
      // Save to sessionStorage for progress page
      sessionStorage.setItem("task_id",     data.task_id);
      sessionStorage.setItem("course",      selectedCourse.name);
      sessionStorage.setItem("icon",        selectedCourse.icon);
      sessionStorage.setItem("filename",    selectedFile.name);
      sessionStorage.setItem("output_file", data.output_file);

      // Go to progress page
      window.location.href = "pages/progress.html";

    } else {
      showUploadError("❌ " + (data.error || "Upload failed"));
      btn.disabled    = false;
      btn.textContent = "🚀 Start Processing";
    }

  } catch (err) {
    showUploadError("❌ Backend not reachable. Is Flask running?");
    btn.disabled    = false;
    btn.textContent = "🚀 Start Processing";
    console.error(err);
  }
}

renderCourses();

// NOTE: index.html modal mein ye div add karo btn-start se pehle:
// <div id="upload-error" style="display:none; color:#ef4444; font-size:13px; font-weight:600; margin-bottom:10px; padding:8px 12px; background:#fef2f2; border-radius:8px; border:1px solid #fecaca;"></div>