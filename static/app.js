document.addEventListener('DOMContentLoaded', () => {
  const refresh = document.getElementById('refresh-prediction');
  if (refresh) refresh.addEventListener('click', async () => {
    const feedback = document.getElementById('prediction-feedback');
    refresh.disabled = true; refresh.textContent = '↻ Updating…'; feedback.textContent = 'Recalculating against current workload…';
    try {
      const response = await fetch(refresh.dataset.url, {method:'POST', headers:{'X-Requested-With':'XMLHttpRequest'}});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not refresh estimate.');
      const value = document.querySelector('.prediction-value'); value.innerHTML = `${Math.round(data.hours)}<small>hours</small>`;
      document.querySelector('.model-label').textContent = data.model; feedback.textContent = `Updated ${data.predicted_at}.`;
    } catch (err) { feedback.textContent = err.message; }
    finally { refresh.disabled = false; refresh.innerHTML = '↻ Refresh estimate'; }
  });
  if (window.analyticsData && window.Chart) {
    const d = window.analyticsData;
    const common = {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{backgroundColor:'#182a27', padding:12, cornerRadius:10}}, scales:{x:{grid:{display:false},ticks:{color:'#84918c',font:{family:'DM Sans'}}},y:{beginAtZero:true,grid:{color:'#edf0ed'},ticks:{precision:0,color:'#84918c',font:{family:'DM Sans'}}}}};
    const monthly = document.getElementById('monthlyChart'); if(monthly) new Chart(monthly,{type:'line',data:{labels:d.months,datasets:[{data:d.monthly,borderColor:'#477b6a',backgroundColor:'rgba(71,123,106,.12)',fill:true,tension:.38,pointRadius:4,pointBackgroundColor:'#477b6a',borderWidth:2.5}]},options:common});
    const category = document.getElementById('categoryChart'); if(category) new Chart(category,{type:'doughnut',data:{labels:d.categories,datasets:[{data:d.categoryValues,backgroundColor:['#477b6a','#d7a66c','#8b91bd','#c67e6a','#80a9a0','#c5b5d9','#b8c4bd'],borderWidth:3,borderColor:'#fff',hoverOffset:5}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{position:'bottom',labels:{usePointStyle:true,pointStyle:'circle',padding:16,color:'#65716c',font:{family:'DM Sans',size:11}}}}}});
    const status = document.getElementById('statusChart'); if(status) new Chart(status,{type:'bar',data:{labels:d.statuses,datasets:[{data:d.statusValues,backgroundColor:'#79a392',borderRadius:7,maxBarThickness:38}]},options:{...common,indexAxis:'y',scales:{x:{beginAtZero:true,grid:{color:'#edf0ed'},ticks:{precision:0,color:'#84918c'}},y:{grid:{display:false},ticks:{color:'#65716c'}}}}});
  }
});
