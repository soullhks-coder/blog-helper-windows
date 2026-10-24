"""Offline Chrome regression for the complete four-image Blogger upload sequence.

Runs the production caret, picker, file-transfer and resize functions. All network
requests are intercepted, and temporary WebP files/profile data are disposable.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
from unittest.mock import patch

from PIL import Image


EDITOR = r'''<!doctype html><meta charset="utf-8">
<style>
body {margin:0;padding:88px 20px 30px;background:#f7f7f7}
#toolbar {position:fixed;top:0;left:0;right:0;height:70px;background:white;z-index:10}
#image {margin:18px;padding:8px}
iframe.editable {width:880px;height:610px;background:white;border:1px solid #ccc}
#upload-menu {position:fixed;top:58px;background:white;z-index:30;box-shadow:0 2px 8px #999}
#upload {width:220px;height:42px;line-height:42px;padding:0 12px;cursor:pointer}
#upload:hover {background:#ddd}
#image-controls {position:fixed;top:20px;left:210px;z-index:11}
#size-menu {position:fixed;top:64px;left:265px;background:white;z-index:35}
#extra-large {width:160px;height:40px;position:relative}
#extra-large span {position:absolute;left:40px;top:10px}
#picker {position:fixed;inset:120px 100px;width:660px;height:400px;z-index:40;background:white}
</style>
<div id="toolbar"><button id="image">이미지 삽입</button></div>
<iframe class="editable"></iframe>
<div id="upload-menu" role="menu" hidden>
 <div id="upload" role="menuitem" tabindex="0"><span>컴퓨터에서 업로드</span></div>
</div>
<div id="image-controls" hidden>
 <button id="center">가운데 정렬</button><button id="size">이미지 크기</button>
</div>
<div id="size-menu" role="menu" hidden>
 <div role="menuitemradio" aria-checked="true">중간</div>
 <div id="extra-large" role="menuitemradio" aria-checked="false"><span>매우 크게</span></div>
</div>
<script>
const options=OPTIONS;
window.events=[];window.transfers=[];window.rejected=[];window.uploadRequests=[];
window.editorReady=false;window.fixtureErrors=[];
let activeSlot=null, selectedImage=null, hoverAt=0, downAt=0;
const editable=document.querySelector('iframe.editable');
const menu=document.querySelector('#upload-menu');
const controls=document.querySelector('#image-controls');
const sizes=document.querySelector('#size-menu');
function record(action,extra={}) {events.push({action,time:performance.now(),...extra});}
function documentBody() {return editable.contentDocument.body;}
function slotAtCaret() {
 const selection=editable.contentDocument.getSelection();
 let node=selection.anchorNode;
 if(node?.nodeType===3)node=node.parentElement;
 return node?.closest('[data-slot]')||null;
}
function hideImageControls() {controls.hidden=true;sizes.hidden=true;}
document.querySelector('#image').addEventListener('click',()=>{
 activeSlot=slotAtCaret();hideImageControls();
 record('insert-menu',{slot:activeSlot?.id,scroll:editable.contentWindow.scrollY,
   priorSizes:[...documentBody().querySelectorAll('img')].map(i=>[i.width,i.height])});
 menu.style.left=(24+transfers.length*31)+'px';menu.hidden=false;
 if(transfers.length>=2) {
   const backdrop=document.createElement('div');backdrop.id='backdrop';
   backdrop.style='position:fixed;inset:0;z-index:31;background:transparent';
   document.body.appendChild(backdrop);
   setTimeout(()=>{menu.style.left='165px';},180);
   setTimeout(()=>backdrop.remove(),350);
 }
});
const upload=document.querySelector('#upload');
upload.addEventListener('mouseenter',()=>{hoverAt=performance.now();record('upload-hover');});
upload.addEventListener('mousedown',()=>{downAt=performance.now();record('upload-down');});
function openPicker(method) {
 const index=transfers.length+1;
 uploadRequests.push({index,method});menu.hidden=true;
 record('upload-activation',{index,method});
 if(index>=3 && (method!=='pointer'||performance.now()-hoverAt<150||performance.now()-downAt<100)) {
   rejected.push({index,method});record('upload-rejected',{index});return;
 }
 if(options.blockThird && index===3) {record('picker-blocked',{index});return;}
 const picker=document.createElement('iframe');picker.id='picker';
 picker.src='https://docs.google.com/picker?sequence='+index;
 document.body.appendChild(picker);
}
upload.addEventListener('click',()=>openPicker('pointer'));
upload.addEventListener('keydown',event=>{
 if(event.key==='Enter') {event.preventDefault();openPicker('keyboard');}
});
document.addEventListener('keydown',event=>{
 if(event.key==='Escape') {menu.hidden=true;hideImageControls();document.querySelector('#picker')?.remove();}
});
window.addEventListener('message',event=>{
 if(event.origin!=='https://docs.google.com'||event.data?.type!=='sequence-files')return;
 if(!activeSlot) {fixtureErrors.push('No actual caret at a marker');return;}
 const slot=activeSlot;
 transfers.push({slot:slot.id,files:event.data.files});
 record('files-received',{slot:slot.id,count:event.data.files.length});
 document.querySelector('#picker')?.remove();
 for(const file of event.data.files) {
   const image=editable.contentDocument.createElement('img');
   image.id='uploaded-'+transfers.length;image.dataset.file=file.name;
   image.src='https://blogger.googleusercontent.com/sequence/'+encodeURIComponent(file.name);
   image.width=320;image.height=320;
   image.setAttribute('data-original-width','1024');image.setAttribute('data-original-height','1024');
   image.addEventListener('click',()=>{selectedImage=image;controls.hidden=false;sizes.hidden=true;});
   const paragraph=editable.contentDocument.createElement('p');paragraph.append(image);slot.before(paragraph);
 }
});
document.querySelector('#center').addEventListener('click',()=>{
 if(!selectedImage) {fixtureErrors.push('Center without a selected image');return;}
 selectedImage.parentElement.style.textAlign='center';record('center',{file:selectedImage.dataset.file});
 hideImageControls();
});
document.querySelector('#size').addEventListener('click',()=>{
 document.querySelector('#extra-large').setAttribute('aria-checked','false');sizes.hidden=false;
 record('size-menu',{file:selectedImage.dataset.file});
});
document.querySelector('#extra-large').addEventListener('click',()=>{
 const image=selectedImage;
 document.querySelector('#extra-large').setAttribute('aria-checked','true');sizes.hidden=true;
 record('size-choice',{file:image.dataset.file});
 setTimeout(()=>{
   image.width=640;image.height=640;record('resize-applied',{file:image.dataset.file});
 },220);
});
editable.addEventListener('load',()=>{window.editorReady=true;});
editable.srcdoc=`<!doctype html><meta charset="utf-8"><style>
 body{margin:20px;font:16px sans-serif}p{margin:12px 0}img{display:block}
 </style><body contenteditable="true"><h1>BODY_KEEP_원문 제목</h1>`+
 Array.from({length:4},(_,index)=>`<h2>보존 소제목 ${index+1}</h2>`+
   Array.from({length:index===2?28:8},(_,paragraph)=>
     `<p>BODY_KEEP_${index+1}_${paragraph+1} 기존 문장은 삭제하거나 다시 쓰지 않습니다.</p>`).join('')+
   `<p id="slot-${index}" data-slot="${index}">BLOG_HELPER_IMAGE_SLOT_${index}</p>`).join('')+
 `<p>BODY_KEEP_마지막 본문</p></body>`;
</script>'''

PICKER = r'''<!doctype html><meta charset="utf-8">
<style>body{padding:32px;font:16px sans-serif}button{padding:12px}</style>
<h2>Google 이미지 업로드</h2><button id="browse" hidden>찾아보기</button>
<input id="file" type="file" multiple hidden>
<script>
setTimeout(()=>document.querySelector('#browse').hidden=false,180);
document.querySelector('#browse').addEventListener('click',()=>document.querySelector('#file').click());
document.querySelector('#file').addEventListener('change',async event=>{
 const files=[];
 for(const file of event.target.files) {
   const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
   const sha256=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
   files.push({name:file.name,size:file.size,sha256});
 }
 parent.postMessage({type:'sequence-files',files},'*');
});
</script>'''


def main():
    with tempfile.TemporaryDirectory(prefix="blog-helper-upload-sequence-") as directory, ExitStack() as stack:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app
        import blogspot_upload
        from playwright.sync_api import sync_playwright

        stack.enter_context(patch.object(app, "append_runtime_log"))
        paths=[]
        payloads={}
        for index,color in enumerate(("#df5252","#4b9f57","#4279bf","#aa65bd"),1):
            path=Path(directory)/f"sequence-{index}.webp"
            image=Image.new("RGB",(1024,1024),color)
            image.save(path,"WEBP",quality=88)
            paths.append(path)
            payloads[path.name]=path.read_bytes()

        options={}
        unexpected=[]
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(
                executable_path=str(app.require_google_chrome_executable()),headless=True,
                args=["--disable-background-networking","--disable-component-update"])
            try:
                page=browser.new_page(viewport={"width":1200,"height":900})
                page.set_default_timeout(5_000)

                def respond(route):
                    url=route.request.url
                    if url=="http://127.0.0.1/blogger-sequence":
                        route.fulfill(body=EDITOR.replace("OPTIONS",json.dumps(options)),content_type="text/html")
                    elif url.startswith("https://docs.google.com/picker?sequence="):
                        route.fulfill(body=PICKER,content_type="text/html")
                    elif url.startswith("https://blogger.googleusercontent.com/sequence/"):
                        name=url.rsplit("/",1)[-1]
                        route.fulfill(body=payloads[name],content_type="image/webp")
                    else:
                        unexpected.append(url)
                        route.abort()

                page.route("**/*",respond)
                print("Blogger offline sequential Chrome:",browser.version,flush=True)

                def start(blocked=False):
                    options.clear();options.update(blockThird=blocked)
                    page.goto("http://127.0.0.1/blogger-sequence")
                    page.wait_for_function("editorReady")
                    body=page.frame_locator("iframe.editable").locator("body")
                    return body,body.inner_text()

                def upload(index):
                    app.focus_blogspot_image_slot(page,f"BLOG_HELPER_IMAGE_SLOT_{index}")
                    return app._upload_blogspot_webp_files(page,[str(paths[index])],queue.Queue())

                def verify(body,original,count):
                    assert body.inner_text()==original,"Original article text changed"
                    assert page.evaluate("fixtureErrors")==[],page.evaluate("fixtureErrors")
                    assert not page.evaluate("rejected"),page.evaluate("rejected")
                    transfers=page.evaluate("transfers")
                    assert len(transfers)==count,transfers
                    for index,transfer in enumerate(transfers):
                        path=paths[index];data=payloads[path.name]
                        assert transfer=={"slot":f"slot-{index}","files":[{
                            "name":path.name,"size":len(data),"sha256":hashlib.sha256(data).hexdigest()}]},transfer
                    images=body.locator("img")
                    assert images.count()==count,images.count()
                    assert images.evaluate_all("images=>images.every(image=>image.complete&&image.naturalWidth===1024&&image.width===640&&image.height===640)")
                    assert body.evaluate("body=>[...body.querySelectorAll('img')].every((image,index)=>image.parentElement.nextElementSibling.id==='slot-'+index)")

                body,original=start()
                for index in range(4):
                    assert upload(index)==1
                    verify(body,original,index+1)
                requests=page.evaluate("uploadRequests")
                assert requests==[{"index":index,"method":"pointer"} for index in range(1,5)],requests
                events=page.evaluate("events")
                third=next(event for event in events if event["action"]=="insert-menu" and event["slot"]=="slot-2")
                assert third["scroll"]>600,third
                assert third["priorSizes"]==[[640,640],[640,640]],third
                for index,path in enumerate(paths):
                    choice=next(event for event in events if event["action"]=="size-choice" and event["file"]==path.name)
                    if index<3:
                        following=next(event for event in events if event["action"]=="insert-menu" and event["slot"]==f"slot-{index+1}")
                        assert following["time"]-choice["time"]>=1500,(choice,following)
                print("Blogger offline sequential Chrome: four distinct files once each; third/fourth guarded pointer, scroll and resize passed",flush=True)

                body,original=start(blocked=True)
                # Shorten only the pre-transfer timeout in the negative fixture;
                # the actual retry/selection/file-transfer functions still run.
                with patch.dict(blogspot_upload.open_image_picker.__kwdefaults__,{"wait_seconds":0.6}):
                    try:
                        for index in range(4):
                            upload(index)
                    except blogspot_upload.BlogspotImageUploadError as exc:
                        assert "3회" in str(exc),str(exc)
                    else:
                        raise AssertionError("Blocked third picker unexpectedly continued")
                verify(body,original,2)
                requests=page.evaluate("uploadRequests")
                assert [request["index"] for request in requests]==[1,2,3,3,3],requests
                assert all(request["method"]=="pointer" for request in requests),requests
                assert not page.locator("#picker").count()
                assert body.locator("#slot-3").count()==1
                assert not [event for event in page.evaluate("events") if event["action"]=="insert-menu" and event["slot"]=="slot-3"]
                assert not unexpected,unexpected
                print("Blogger offline sequential Chrome: third picker permanently blocked; two completed images/body preserved, zero third/fourth transfers",flush=True)
            finally:
                browser.close()


if __name__=="__main__":
    main()
