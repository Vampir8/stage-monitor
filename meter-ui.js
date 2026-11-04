/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Shared meter geometry for built-in adapters and configurable controls. */
window.StageMeters = (() => {
  const node=(tag,cls='',text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  function measurement(className,value,unit='') {
    const e=node('div',className+' measurement');e.append(node('span','measurement-number',value));
    if(unit)e.append(node('span','measurement-unit',unit));e.title=value+(unit?' '+unit:'');return e;
  }
  function make(metric,{label='',range=null,kind='audio',horizontal=false,active=true,peak=null,rms=null}={}) {
    const item=node('div','meter-item'+(horizontal?' meter-horizontal':'')),head=node('div','meter-head');
    const number=typeof metric.value==='number'&&Number.isFinite(metric.value),unit=metric.unit&&metric.unit!=='raw'?metric.unit:'';
    const value=number?metric.value.toLocaleString(window.StageI18n?.locale || 'en-GB',{maximumFractionDigits:1}):'—',text=value+(unit?' '+unit:'');
    head.append(node('div','meter-label',label),measurement('meter-value-top',value,unit));
    const track=node('div','meter-track meter-'+kind),fill=node('div','meter-fill');
    const valid=active&&metric.quality==='good'&&number&&Array.isArray(range)&&range.length===2&&range.every(Number.isFinite)&&range[1]>range[0];
    for(const zone of ['red','yellow','green'])track.append(node('div','meter-zone zone-'+zone));
    track.setAttribute('aria-label',label+': '+(valid?text:stageText("no current data")));
    if(valid) {
      const percent=v=>Math.max(0,Math.min(100,(v-range[0])/(range[1]-range[0])*100));
      track.setAttribute('role','meter');track.setAttribute('aria-valuemin',range[0]);track.setAttribute('aria-valuemax',range[1]);
      track.setAttribute('aria-valuenow',Math.max(range[0],Math.min(range[1],metric.value)));track.setAttribute('aria-valuetext',text);
      fill.style.height='100%';fill.style.clipPath=horizontal?`inset(0 ${100-percent(metric.value)}% 0 0)`:`inset(${100-percent(metric.value)}% 0 0 0)`;
      for(const [sample,name] of [[rms,'rms'],[peak,'peak']])if(sample?.quality==='good'&&typeof sample.value==='number'&&Number.isFinite(sample.value)) {
        const marker=node('div','meter-'+name+'-marker');marker.style[horizontal?'left':'bottom']=`calc(${percent(sample.value)}% - 2px)`;track.append(marker);
        track.title=(rms?stageText("Peak"):'RMS')+': '+text+' · '+name.toUpperCase()+': '+sample.value.toFixed(1)+' '+unit;
        track.setAttribute('aria-valuetext',track.title);
      }
    } else {track.classList.add('level-unavailable');fill.hidden=true;}
    track.append(fill);item.append(head,track);return item;
  }
  return {make,measurement};
})();
