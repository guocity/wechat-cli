#include <memory>
#include <new>
#include <string>

static_assert(sizeof(std::shared_ptr<void>) == 16, "Unsupported shared_ptr ABI");
static_assert(sizeof(std::string) == 24, "Unsupported string ABI");

#include <unordered_map>
#include <vector>
#include <cstring>
#include <stdexcept>
#include <cstdint>
#include <fstream>
#include <dlfcn.h>
#include <filesystem>
#include <set>
#include <mutex>
#include <functional>
#include <chrono>
#include <unistd.h>
#include <sys/stat.h>

using Chats = std::unordered_map<std::string, std::pair<std::string,std::string>>;
static_assert(sizeof(Chats) == 40, "Unsupported hash-map ABI");
using SharedGetter = std::shared_ptr<void> (*)(void *);
using ChatGetter = Chats (*)();
struct NameFunctions { void *services; void *contacts; void *displayName; };
using NameGetter = std::string (*)(void *,const std::string &,bool);
static std::shared_ptr<void> contactService(const std::shared_ptr<void> &account,const NameFunctions &api) {
    const auto services=reinterpret_cast<SharedGetter>(api.services)(account.get());
    return services ? reinterpret_cast<SharedGetter>(api.contacts)(services.get()) : nullptr;
}
static std::string displayName(const std::shared_ptr<void> &contacts,const NameFunctions &api,const std::string &id) {
    return contacts && !id.empty() ? reinterpret_cast<NameGetter>(api.displayName)(contacts.get(),id,false) : "";
}
static thread_local std::string bridgeResult;
static thread_local unsigned bridgeStage=0;
extern "C" unsigned bridge_last_stage(){return bridgeStage;}

static void quote(std::string &out, const std::string &value, size_t maximum = 65536) {
    if (value.size() > maximum) throw std::runtime_error("native_field_too_large");
    out += '"';
    const char hex[] = "0123456789abcdef";
    for (unsigned char ch : value) {
        if (ch == '"' || ch == '\\') { out += '\\'; out += ch; }
        else if (ch < 32) { out += "\\u00"; out += hex[ch >> 4]; out += hex[ch & 15]; }
        else out += ch;
    }
    out += '"';
    if (out.size() > 16 * 1024 * 1024) throw std::runtime_error("native_result_too_large");
}
struct alignas(8) RawRecord {
    unsigned char bytes[0x278];
    ~RawRecord() {
        void **vtable; std::memcpy(&vtable,bytes,sizeof(vtable));
        if (vtable) reinterpret_cast<void (*)(void *)>(vtable[0])(this);
    }
};
static_assert(sizeof(RawRecord) == 0x278, "Unsupported record stride");
using Records = std::vector<RawRecord>;
using MessageGetter = Records (*)(void *,const std::string &,uint32_t,void *);
static thread_local void *sessionDestroy;
struct alignas(8) SessionRecord {
    unsigned char bytes[0x218];
    ~SessionRecord(){reinterpret_cast<void (*)(void *)>(sessionDestroy)(this);}
};
using Sessions=std::unordered_map<std::string,SessionRecord>;
static_assert(sizeof(Sessions)==40,"Unsupported session-map ABI");
struct RecentFunctions { void *services; void *messages; void *initial; void *sessionGetter;
    void *sessionMarker; void *copySessions; void *destroySession; void *isFolded; void *isPinned; };
extern "C" const char *bridge_read_chats_v2(void *enumeration, void *accountGetter, void *manager,void *nameFunctions,void *recentFunctions) {
    try {
        const auto account = reinterpret_cast<SharedGetter>(accountGetter)(manager);
        if (!account) { bridgeResult = "{\"error\":\"native_account_not_ready\"}"; return bridgeResult.c_str(); }
        const auto chats = reinterpret_cast<ChatGetter>(enumeration)();
        const auto *names=static_cast<const NameFunctions *>(nameFunctions);
        const auto contacts=names ? contactService(account,*names) : nullptr;
        const auto *recent=static_cast<const RecentFunctions *>(recentFunctions);
        const auto services=recent ? reinterpret_cast<SharedGetter>(recent->services)(account.get()) : nullptr;
        const auto messages=services ? reinterpret_cast<SharedGetter>(recent->messages)(services.get()) : nullptr;
        if(recent&&!messages)throw std::runtime_error("native_message_service_not_ready");
        // Same service, snapshot copy and record destructor used by native
        // GetAllSessionInf. Read folder membership from the native predicate.
        Sessions sessions;
        std::unordered_map<std::string,bool> foldedSessions,pinnedSessions;
        if(recent){
            void *marker=recent->sessionMarker;
            using ServiceGetter=std::shared_ptr<void> (*)(void *,void *);
            auto **accountVtable=*reinterpret_cast<void ***>(account.get());
            const auto sessionServices=reinterpret_cast<SharedGetter>(accountVtable[0x38/8])(account.get());
            if(!sessionServices)throw std::runtime_error("native_session_service_not_ready");
            const auto sessionService=reinterpret_cast<ServiceGetter>(recent->sessionGetter)(sessionServices.get(),&marker);
            if(!sessionService)throw std::runtime_error("native_session_service_not_ready");
            auto **vtable=*reinterpret_cast<void ***>(sessionService.get());
            const auto *snapshot=reinterpret_cast<const void *(*)(void *)>(vtable[0x78/8])(sessionService.get());
            if(!snapshot)throw std::runtime_error("native_session_service_not_ready");
            sessionDestroy=recent->destroySession;
            reinterpret_cast<void (*)(void *,const void *)>(recent->copySessions)(&sessions,snapshot);
            if(sessions.size()>20000)throw std::runtime_error("native_result_too_large");
            for(const auto &record:sessions){
                const auto &id=record.first;
                if(id.empty()||id.size()>1024)throw std::runtime_error("native_session_layout_unverified");
                foldedSessions.emplace(id,reinterpret_cast<bool (*)(const void *)>(recent->isFolded)(record.second.bytes));
                pinnedSessions.emplace(id,reinterpret_cast<bool (*)(const void *)>(recent->isPinned)(record.second.bytes));
            }
        }
        alignas(8) unsigned char options[80]={};float factor=1;std::memcpy(options+32,&factor,4);
        if (chats.size() > 20000) throw std::runtime_error("native_result_too_large");
        std::string json = "{\"source\":\"native\",\"total\":" + std::to_string(chats.size()) + ",\"items\":[";
        bool first = true;
        for (const auto &chat : chats) {
            if (chat.second.first.empty()) throw std::runtime_error("empty_conversation_id");
            if (!first) json += ','; first = false;
            json += "{\"id\":"; quote(json,chat.second.first,1024);
            json += ",\"displayString\":"; quote(json,chat.second.second);
            json += ",\"displayName\":"; quote(json,names ? displayName(contacts,*names,chat.second.first) : "");
            if(recent) {
                const auto entry=foldedSessions.find(chat.second.first);
                if(entry==foldedSessions.end())throw std::runtime_error("native_session_layout_unverified");
                const bool folded=entry->second;
                json += ",\"isPinned\":"+std::string(pinnedSessions.at(chat.second.first)?"true":"false");
                json += ",\"isMinimized\":"+std::string(folded?"true":"false");
                const auto records=reinterpret_cast<MessageGetter>(recent->initial)(messages.get(),chat.second.first,1,options);
                if(records.size()>1)throw std::runtime_error("native_message_layout_unverified");
                uint32_t latest=0;
                if(!records.empty())std::memcpy(&latest,records.front().bytes+0x114,4);
                json += ",\"lastMessageTime\":"+std::to_string(latest);
            }
            json += ",\"source\":\"native\"}";
        }
        json += "]}"; bridgeResult = std::move(json);
    } catch (const std::runtime_error &error) {
        bridgeResult="{\"error\":";quote(bridgeResult,error.what(),1024);bridgeResult+='}';
    } catch (...) { bridgeResult = "{\"error\":\"native_conversation_layout_unverified\"}"; }
    return bridgeResult.c_str();
}
static thread_local void *uiDestroy;
struct alignas(8) UiRecord {
    unsigned char bytes[0x350];
    ~UiRecord(){reinterpret_cast<void (*)(void *)>(uiDestroy)(this);}
};
static_assert(sizeof(UiRecord)==0x350,"Unsupported UI record stride");
using UiRecords=std::vector<UiRecord>;
static uint64_t uiInteger64(const UiRecord &record,size_t offset);
static const std::string &uiField(const UiRecord &record,size_t offset);
struct VoiceTask {std::function<void(const void *)> factory;std::weak_ptr<void> owner;uint64_t token;};
struct VoiceHandle {uint64_t id;std::shared_ptr<void> resolver;std::shared_ptr<void> owner;};
struct VoiceLocation {const char *name;const char *file;uint64_t line;void *caller;};
static_assert(sizeof(VoiceTask)==56&&sizeof(VoiceHandle)==40&&sizeof(VoiceLocation)==32,"Unsupported task ABI");
struct VoiceJob {std::mutex mutex;std::string text;std::string state="pending";VoiceHandle handle{};std::chrono::steady_clock::time_point created=std::chrono::steady_clock::now();};
static std::mutex voiceJobsMutex;
static std::unordered_map<std::string,std::shared_ptr<VoiceJob>> voiceJobs;
struct TranscribeFunctions {void *initial;void *convert;void *destroy;void *registry;void *descriptor;void *start;void *attach;};
static std::string voiceJobKey(void *account,const char *chat,const char *id){return std::to_string(reinterpret_cast<uintptr_t>(account))+"|"+chat+"|"+id;}
static std::string voiceJobJson(const std::shared_ptr<VoiceJob> &job){
    std::lock_guard<std::mutex> guard(job->mutex);
    if(job->state=="pending"&&std::chrono::steady_clock::now()-job->created>std::chrono::seconds(90))job->state="timed_out";
    std::string json="{\"status\":";quote(json,job->state);json+=",\"text\":";if(job->state=="completed")quote(json,job->text);else json+="null";json+="}";return json;
}
extern "C" const char *bridge_voice_transcription_result(void *accountGetter,void *manager,const char *chat,const char *id){
    try{const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);if(!account)throw std::runtime_error("native_account_not_ready");
        std::lock_guard<std::mutex> guard(voiceJobsMutex);const auto found=voiceJobs.find(voiceJobKey(account.get(),chat,id));
        bridgeResult=found==voiceJobs.end()?"{\"status\":\"unavailable\",\"text\":null}":voiceJobJson(found->second);
    }catch(...){bridgeResult="{\"error\":\"native_account_not_ready\"}";}return bridgeResult.c_str();
}
extern "C" const char *bridge_start_voice_transcription(void *accountGetter,void *manager,void *servicesGetter,void *messageServiceGetter,void *functions,const char *chat,const char *id){
    try{
        const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);if(!account)throw std::runtime_error("native_account_not_ready");
        const auto services=reinterpret_cast<SharedGetter>(servicesGetter)(account.get());
        const auto service=services?reinterpret_cast<SharedGetter>(messageServiceGetter)(services.get()):nullptr;
        if(!service)throw std::runtime_error("native_message_service_not_ready");
        const auto &api=*static_cast<const TranscribeFunctions *>(functions);
        const auto key=voiceJobKey(account.get(),chat,id);
        std::unique_lock<std::mutex> jobsGuard(voiceJobsMutex);
        for(auto it=voiceJobs.begin();it!=voiceJobs.end();){if(std::chrono::steady_clock::now()-it->second->created>std::chrono::minutes(5))it=voiceJobs.erase(it);else ++it;}
        const auto old=voiceJobs.find(key);if(old!=voiceJobs.end()){bridgeResult=voiceJobJson(old->second);return bridgeResult.c_str();}
        size_t pending=0;for(const auto &entry:voiceJobs){std::lock_guard<std::mutex> guard(entry.second->mutex);pending+=entry.second->state=="pending";}
        if(pending>=2||voiceJobs.size()>=32){bridgeResult="{\"error\":\"native_bridge_busy\"}";return bridgeResult.c_str();}
        const std::string chatId(chat);alignas(8) unsigned char options[80]={};float factor=1;std::memcpy(options+32,&factor,4);options[0x4c]=1;
        const auto raw=reinterpret_cast<MessageGetter>(api.initial)(service.get(),chatId,100,options);
        uiDestroy=api.destroy;const auto records=reinterpret_cast<UiRecords (*)(const std::string &,const Records &)>(api.convert)(chatId,raw);
        const UiRecord *selected=nullptr;for(const auto &record:records)if(std::to_string(uiInteger64(record,0x90))==id){selected=&record;break;}
        if(!selected){bridgeResult="{\"error\":\"native_message_not_found\"}";return bridgeResult.c_str();}
        uint32_t type;std::memcpy(&type,selected->bytes+8,4);if(type!=34){bridgeResult="{\"error\":\"invalid_media_type\"}";return bridgeResult.c_str();}
        const auto job=std::make_shared<VoiceJob>();
        const auto &cached=uiField(*selected,0x108);if(!cached.empty()){job->state="completed";job->text=cached;voiceJobs.emplace(key,job);bridgeResult=voiceJobJson(job);return bridgeResult.c_str();}
        auto **vtable=*reinterpret_cast<void ***>(account.get());
        const auto frontServices=reinterpret_cast<SharedGetter>(vtable[0x30/8])(account.get());
        void *descriptor=api.descriptor;
        using Registry=std::shared_ptr<void> (*)(void *,void *);
        const auto front=frontServices?reinterpret_cast<Registry>(api.registry)(frontServices.get(),&descriptor):nullptr;
        if(!front){bridgeResult="{\"error\":\"native_services_not_ready\"}";return bridgeResult.c_str();}
        using Start=VoiceTask (*)(void *,const void *);
        auto task=reinterpret_cast<Start>(api.start)(front.get(),selected->bytes);
        std::weak_ptr<VoiceJob> weak=job;
        std::function<void(const std::string &)> success=[weak](const std::string &text){if(auto value=weak.lock()){std::lock_guard<std::mutex> guard(value->mutex);if(value->state=="pending"){value->text=text;value->state=text.empty()?"failed":"completed";}}};
        std::function<void(const void *)> failure=[weak](const void *){if(auto value=weak.lock()){std::lock_guard<std::mutex> guard(value->mutex);if(value->state=="pending")value->state="failed";}};
        std::function<void()> finished;
        const VoiceLocation location{"TranscribeVoice","native-api",1,nullptr};
        using Attach=VoiceHandle (*)(VoiceTask *,void *,void *,void *,const VoiceLocation *);
        voiceJobs.emplace(key,job);jobsGuard.unlock();
        job->handle=reinterpret_cast<Attach>(api.attach)(&task,&success,&failure,&finished,&location);
        bridgeResult=voiceJobJson(job);
    }catch(...){bridgeResult="{\"error\":\"native_transcription_failed\"}";}return bridgeResult.c_str();
}
static std::mutex mediaPathMutex;
static std::unordered_map<std::string,std::filesystem::path> nativeMediaPaths;
static std::unordered_map<std::string,std::string> nativeMediaBlobs;
static_assert(sizeof(std::filesystem::path)==24,"Unsupported native path ABI");
struct OcrFunctions {void *getter;void *initialize;void *recognize;};
struct OcrResult {std::shared_ptr<void> text;std::shared_ptr<void> qr;std::shared_ptr<void> extra;};
static_assert(sizeof(OcrResult)==48,"Unsupported OCR result ABI");
static std::mutex ocrMutex;
struct OcrTemporaryFile {
    std::string path;
    ~OcrTemporaryFile(){if(!path.empty())::unlink(path.c_str());}
};
extern "C" const char *bridge_extract_image_text(void *accountGetter,void *manager,void *servicesGetter,void *functions,const char *encoded){
    try{
        std::unique_lock<std::mutex> guard(ocrMutex,std::try_to_lock);if(!guard.owns_lock())throw std::runtime_error("native_bridge_busy");
        const size_t inputSize=std::strlen(encoded);if(inputSize==0||inputSize>11184812||inputSize%4)throw std::runtime_error("native_invalid_ocr_image");
        std::string bytes;bytes.reserve(inputSize/4*3);unsigned int accumulator=0,bits=0;bool padding=false;
        for(size_t i=0;i<inputSize;i++){
            const unsigned char c=encoded[i];if(c=='='){padding=true;continue;}if(padding)throw std::runtime_error("native_invalid_ocr_image");
            int value=c>='A'&&c<='Z'?c-'A':c>='a'&&c<='z'?c-'a'+26:c>='0'&&c<='9'?c-'0'+52:c=='+'?62:c=='/'?63:-1;
            if(value<0)throw std::runtime_error("native_invalid_ocr_image");accumulator=(accumulator<<6)|static_cast<unsigned int>(value);bits+=6;
            if(bits>=8){bits-=8;bytes+=static_cast<char>((accumulator>>bits)&255);}
        }
        if(bytes.empty()||bytes.size()>8*1024*1024)throw std::runtime_error("native_invalid_ocr_image");
        const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);if(!account)throw std::runtime_error("native_account_not_ready");
        const auto services=reinterpret_cast<SharedGetter>(servicesGetter)(account.get());
        const auto &api=*static_cast<const OcrFunctions *>(functions);
        const auto ocr=services?reinterpret_cast<SharedGetter>(api.getter)(services.get()):nullptr;
        if(!ocr)throw std::runtime_error("native_services_not_ready");
        uint32_t state;std::memcpy(&state,static_cast<const unsigned char *>(ocr.get())+0x20,4);
        if(state!=1){reinterpret_cast<void (*)(void *)>(api.initialize)(ocr.get());std::memcpy(&state,static_cast<const unsigned char *>(ocr.get())+0x20,4);}
        if(state!=1)throw std::runtime_error("native_ocr_not_ready");
        OcrTemporaryFile temporary;
        const auto pattern=(std::filesystem::temp_directory_path()/"wechat-api-ocr-XXXXXX").string();
        std::vector<char> filename(pattern.begin(),pattern.end());filename.push_back(0);
        int fd=::mkstemp(filename.data());if(fd<0)throw std::runtime_error("native_ocr_failed");temporary.path=filename.data();
        size_t written=0;while(written<bytes.size()){const auto n=::write(fd,bytes.data()+written,bytes.size()-written);if(n<=0){::close(fd);throw std::runtime_error("native_ocr_failed");}written+=static_cast<size_t>(n);}::close(fd);
        alignas(8) unsigned char options[32]={};options[0]=1;options[1]=1;uint32_t type=1;std::memcpy(options+8,&type,4);
        const std::filesystem::path path(temporary.path);
        // Direct local IPC entry: deliberately bypasses DoOCRQR's network fallback.
        using Recognize=OcrResult (*)(void *,const std::filesystem::path &,const void *);
        const auto result=reinterpret_cast<Recognize>(api.recognize)(ocr.get(),path,options);
        if(!result.text)throw std::runtime_error("native_ocr_failed");
        const auto *value=static_cast<const unsigned char *>(result.text.get());
        uintptr_t begin,end;std::memcpy(&begin,value+8,8);std::memcpy(&end,value+0x10,8);
        if(end<begin||(end-begin)%0x80||(end-begin)/0x80>10000||(end!=begin&&begin==0))throw std::runtime_error("native_ocr_layout_unverified");
        std::string lines="[",text;size_t total=0;bool first=true;
        for(uintptr_t line=begin;line<end;line+=0x80){
            const auto &part=*reinterpret_cast<const std::string *>(line+0x18);if(part.size()>65536||total+part.size()>1048576)throw std::runtime_error("native_ocr_layout_unverified");total+=part.size();
            if(!first){lines+=',';text+='\n';}first=false;quote(lines,part,65536);text+=part;
        }lines+=']';
        const auto &combined=*reinterpret_cast<const std::string *>(value+0x30);if(!combined.empty()){if(combined.size()>1048576)throw std::runtime_error("native_ocr_layout_unverified");text=combined;}
        bridgeResult="{\"text\":";quote(bridgeResult,text,1048576);bridgeResult+=",\"lines\":"+lines+",\"recognition\":\"wechat\",\"processing\":\"local\"}";
    }catch(const std::runtime_error &error){bridgeResult="{\"error\":";quote(bridgeResult,error.what());bridgeResult+='}';}
    catch(...){bridgeResult="{\"error\":\"native_ocr_failed\"}";}
    return bridgeResult.c_str();
}
struct MessageFunctions {void *initial;void *convert;void *destroy;NameFunctions names;void *voicePath;void *voiceData;};
static uint64_t uiInteger64(const UiRecord &record,size_t offset){uint64_t value;std::memcpy(&value,record.bytes+offset,8);return value;}
static const std::string &uiField(const UiRecord &record,size_t offset){return *reinterpret_cast<const std::string *>(record.bytes+offset);}
extern "C" const char *bridge_read_messages(void *accountGetter,void *manager,void *servicesGetter,
        void *messageServiceGetter,void *messageFunctions,const char *chatId,uint32_t limit,const char *mediaMessageId) {
    try {
        bridgeStage=1;const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);
        if(!account) {bridgeResult="{\"error\":\"native_account_not_ready\"}";return bridgeResult.c_str();}
        bridgeStage=2;const auto services=reinterpret_cast<SharedGetter>(servicesGetter)(account.get());
        if(!services) {bridgeResult="{\"error\":\"native_services_not_ready\"}";return bridgeResult.c_str();}
        bridgeStage=3;const auto service=reinterpret_cast<SharedGetter>(messageServiceGetter)(services.get());
        if(!service){bridgeResult="{\"error\":\"native_message_service_not_ready\"}";return bridgeResult.c_str();}
        std::string id(chatId);alignas(8) unsigned char options[80]={};float factor=1;std::memcpy(options+32,&factor,4);
        // Installed build's history backend calls RawMsgInfo::ClearContent
        // unless this request flag is set (verified in the installed history backend).
        options[0x4c]=1;
        const auto &api=*static_cast<const MessageFunctions *>(messageFunctions);
        const auto contacts=contactService(account,api.names);
        std::unordered_map<std::string,std::string> nameCache;
        const auto nameFor=[&](const std::string &sender) -> const std::string & {
            auto entry=nameCache.find(sender);
            if(entry==nameCache.end()) entry=nameCache.emplace(sender,displayName(contacts,api.names,sender)).first;
            return entry->second;
        };
        bridgeStage=4;const auto rawRecords=reinterpret_cast<MessageGetter>(api.initial)(service.get(),id,limit,options);
        using Convert=UiRecords (*)(const std::string &,const Records &);
        uiDestroy=api.destroy;
        bridgeStage=5;const auto records=reinterpret_cast<Convert>(api.convert)(id,rawRecords);
        if(records.size()>limit) throw std::runtime_error("native_result_too_large");
        std::string json="{\"source\":\"native\",\"chatId\":";quote(json,id,1024);
        json+=",\"total\":"+std::to_string(records.size())+",\"limit\":"+std::to_string(limit)+",\"items\":[";
        std::unordered_map<uint64_t,const RawRecord *> rawById;
        for(const auto &raw:rawRecords){uint64_t key;std::memcpy(&key,raw.bytes+0xf8,8);rawById.emplace(key,&raw);}
        bool first=true;
        bridgeStage=7;for(const auto &record:records) {
            if(!first)json+=',';first=false;
            uint32_t type;std::memcpy(&type,record.bytes+8,4);
            json+="{\"id\":";quote(json,std::to_string(uiInteger64(record,0x90)));
            json+=",\"serverId\":";quote(json,std::to_string(uiInteger64(record,0xa0)));
            json+=",\"type\":"+std::to_string(type)+",\"from\":";quote(json,uiField(record,0x10),1024);
            json+=",\"to\":";quote(json,uiField(record,0x28),1024);
            json+=",\"groupSender\":";quote(json,uiField(record,0x40),1024);
            const auto &groupSender=uiField(record,0x40);
            json+=",\"senderName\":";quote(json,nameFor(groupSender.empty()?uiField(record,0x10):groupSender));
            uint32_t timestamp;std::memcpy(&timestamp,record.bytes+0xa8,4);
            json+=",\"timestamp\":"+std::to_string(timestamp);
            const auto raw=rawById.find(uiInteger64(record,0x90));
            const auto &displayContent=uiField(record,0xb8);
            const auto &originalContent=raw!=rawById.end()?*reinterpret_cast<const std::string *>(raw->second->bytes+0x130):displayContent;
            json+=",\"content\":";quote(json,displayContent.empty()?originalContent:displayContent,1048576);
            if(type==3||type==34||type==43||type==47||type==49||type==62){
                json+=",\"nativeMediaFields\": {";bool firstField=true;
                if(uiInteger64(record,0xa0)!=0){
                    using Md5=unsigned char *(*)(const void *,uint32_t,unsigned char *);
                    auto md5=reinterpret_cast<Md5>(dlsym(RTLD_DEFAULT,"CC_MD5"));
                    if(md5){const auto server=std::to_string(uiInteger64(record,0xa0));unsigned char digest[16];md5(server.data(),server.size(),digest);std::string hash;const char digits[]="0123456789abcdef";for(unsigned char c:digest){hash+=digits[c>>4];hash+=digits[c&15];}
                        quote(json,"serverHash");json+=':';quote(json,hash);json+=',';quote(json,"serverHashMiddle");json+=':';quote(json,hash.substr(8,16));firstField=false;
                    }
                }
                if(type==34&&mediaMessageId&&std::to_string(uiInteger64(record,0x90))==mediaMessageId&&raw!=rawById.end()){
                    const auto key="native_voice_"+std::to_string(uiInteger64(record,0x90))+"_"+std::to_string(uiInteger64(record,0xa0));bool available=false;
                    using VoiceGetter=std::shared_ptr<void> (*)(void *,const void *,const std::string &);
                    const std::string index="0";
                    const auto voice=reinterpret_cast<VoiceGetter>(api.voiceData)(service.get(),raw->second->bytes,index);
                    if(voice){
                        const auto &buffer=*reinterpret_cast<const std::shared_ptr<std::vector<unsigned char>> *>(static_cast<const unsigned char *>(voice.get())+0x60);
                        if(buffer&&!buffer->empty()&&buffer->size()<=1024*1024){
                            const std::string bytes(reinterpret_cast<const char *>(buffer->data()),buffer->size());
                            std::lock_guard<std::mutex> guard(mediaPathMutex);if(nativeMediaBlobs.size()>=32)nativeMediaBlobs.clear();nativeMediaBlobs[key]=bytes;available=true;
                        }
                    }
                    if(!available&&api.voicePath){
                        // REA/ARM64: hidden x8 path result, x0 MsgId (raw+0xf8),
                        // x1 RawMsgInfo, w2 format (4=SILK).
                        using PathGetter=std::filesystem::path (*)(const void *,const void *,uint32_t);
                        const auto path=reinterpret_cast<PathGetter>(api.voicePath)(raw->second->bytes+0xf8,raw->second->bytes,4);
                        std::error_code error;if(path.is_absolute()&&std::filesystem::is_regular_file(path,error)){
                            std::lock_guard<std::mutex> guard(mediaPathMutex);if(nativeMediaPaths.size()>1000)nativeMediaPaths.clear();nativeMediaPaths[key]=path;available=true;
                        }
                    }
                    if(available){if(!firstField)json+=',';quote(json,"voiceFile");json+=':';quote(json,key);firstField=false;}
                }
                for(const size_t offset:{0x78,0xd0,0x108,0x120,0x140,0x158,0x170,0x188,0x1a0,0x1b8,0x1e0,0x1f8}){
                    const auto &field=uiField(record,offset);if(field.empty())continue;
                    if(!firstField)json+=',';firstField=false;
                    quote(json,std::to_string(offset));json+=':';quote(json,field);
                }json+='}';
            }
            json+=",\"source\":\"native\",\"chatId\":";quote(json,id,1024);json+='}';
        }
        bridgeStage=8;json+="]}";bridgeResult=std::move(json);
    } catch(...) {bridgeResult="{\"error\":\"native_message_layout_unverified\"}";}
    return bridgeResult.c_str();
}

extern "C" bool bridge_has_task_context(void *descriptor) {
    auto resolver=*static_cast<void *(**)(void *)>(descriptor);
    void *slot=resolver(descriptor);
    return slot && *static_cast<void **>(slot)!=nullptr;
}

struct SendFunctions { void *uuid; void *construct; void *send; void *destroyResult; };
struct alignas(8) SendResult {unsigned char bytes[0x40];};
extern "C" const char *bridge_probe_text_processor_v2(void *accountGetter,void *manager,
        void *servicesGetter,void *messageServiceGetter,void *processorGetter) {
    try {
        const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);
        const auto services=account?reinterpret_cast<SharedGetter>(servicesGetter)(account.get()):nullptr;
        const auto service=services?reinterpret_cast<SharedGetter>(messageServiceGetter)(services.get()):nullptr;
        if(!service)throw std::runtime_error("native_message_service_not_ready");
        const uint32_t type=1,subtype=0;
        using Getter=std::shared_ptr<void> (*)(void *,const uint32_t *,const uint32_t *);
        const auto processor=reinterpret_cast<Getter>(processorGetter)(service.get(),&type,&subtype);
        bridgeResult="{\"source\":\"native\",\"textProcessorAvailable\":"+std::string(processor?"true":"false")+",\"processorVtable\":\""+std::to_string(processor?reinterpret_cast<uintptr_t>(*reinterpret_cast<void **>(processor.get())):0)+"\"}";
    }catch(...){bridgeResult="{\"error\":\"native_processor_probe_failed\"}";}
    return bridgeResult.c_str();
}
static const char *bridge_text_v2(void *accountGetter,void *manager,void *servicesGetter,
        void *messageServiceGetter,void *functions,const char *chatId,const char *content,bool prepare) {
    try {
        // The caller validates this recipient against the active conversation list.
        const std::string id(chatId),text(content);

        const auto &api=*static_cast<const SendFunctions *>(functions);
        const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);
        if(!account)throw std::runtime_error("native_account_not_ready");
        const auto services=reinterpret_cast<SharedGetter>(servicesGetter)(account.get());
        const auto service=services?reinterpret_cast<SharedGetter>(messageServiceGetter)(services.get()):nullptr;
        if(!service)throw std::runtime_error("native_message_service_not_ready");
        const auto uuid=reinterpret_cast<std::string (*)()>(api.uuid)();
        // Native UiSendInfo factory owns the embedded 0x350-byte UiRecord.
        // The send processor requires this type; RawSendInfo fails its cast.
        const auto owned=reinterpret_cast<std::shared_ptr<void> (*)()>(api.construct)();
        if(!owned)throw std::runtime_error("native_send_info_empty");
        auto *info=static_cast<unsigned char *>(owned.get());
        auto *record=info+0xe8;
        auto **managerVtable=*reinterpret_cast<void ***>(manager);
        const auto &self=reinterpret_cast<const std::string &(*)(void *)>(managerVtable[0x28/8])(manager);
        if(self.empty())throw std::runtime_error("native_account_not_ready");
        *reinterpret_cast<std::string *>(info+0x90)=id;
        uint32_t type=1;std::memcpy(record+8,&type,4);
        *reinterpret_cast<std::string *>(record+0x10)=self;
        *reinterpret_cast<std::string *>(record+0x28)=id;
        *reinterpret_cast<std::string *>(record+0xb8)=text;
        const bool receiverVerified=*reinterpret_cast<const std::string *>(info+0x90)==id&&*reinterpret_cast<const std::string *>(record+0x28)==id;
        if(prepare){
            bridgeResult="{\"source\":\"native\",\"prepared\":true,\"queued\":false,\"receiverVerified\":"+std::string(receiverVerified?"true":"false")+",\"requestId\":";
            quote(bridgeResult,uuid);bridgeResult+="}";return bridgeResult.c_str();
        }
        const std::vector<std::shared_ptr<void>> requests{owned};
        using Send=SendResult (*)(void *,const std::vector<std::shared_ptr<void>> &);
        auto result=reinterpret_cast<Send>(api.send)(service.get(),requests);
        const bool queued=result.bytes[0]==1;
        const std::string reason=*reinterpret_cast<const std::string *>(result.bytes+8);
        // Both success and error paths own native result strings/vectors.
        reinterpret_cast<void (*)(void *)>(api.destroyResult)(&result);
        bridgeResult="{\"source\":\"native\",\"queued\":"+std::string(queued?"true":"false")+",\"requestId\":";
        quote(bridgeResult,uuid);bridgeResult+=",\"requestKind\":\"ui\",\"receiverVerified\":"+std::string(receiverVerified?"true":"false");bridgeResult+=",\"nativeError\":";quote(bridgeResult,reason,4096);bridgeResult+="}";
    }catch(...) {bridgeResult="{\"error\":\"native_send_failed\"}";}
    return bridgeResult.c_str();
}

extern "C" const char *bridge_send_text_v2(void *a,void *b,void *c,void *d,void *e,const char *f,const char *g){return bridge_text_v2(a,b,c,d,e,f,g,false);}
extern "C" const char *bridge_prepare_text_v2(void *a,void *b,void *c,void *d,void *e,const char *f,const char *g){return bridge_text_v2(a,b,c,d,e,f,g,true);}

struct VoiceSendOptions {uint64_t first=0;uint64_t second=0;uint8_t flag=0;uint8_t padding[7]={};std::string extra;};
static_assert(sizeof(VoiceSendOptions)==48,"Unsupported voice send options ABI");
extern "C" const char *bridge_send_voice(void *accountGetter,void *manager,void *servicesGetter,
        void *messageServiceGetter,void *functions,const char *chatId,const void *data,uint32_t size,uint32_t duration,bool prepare) {
    bool submitted=false;
    try {
        if(!data||size<10||size>1024*1024||duration<1000||duration>60000)throw std::runtime_error("invalid_voice");
        const std::string bytes(static_cast<const char *>(data),size),id(chatId);
        if(bytes.compare(0,10,"\x02#!SILK_V3")!=0&&bytes.compare(0,9,"#!SILK_V3")!=0)throw std::runtime_error("invalid_voice");
        const auto &api=*static_cast<const SendFunctions *>(functions);
        const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);
        const auto services=account?reinterpret_cast<SharedGetter>(servicesGetter)(account.get()):nullptr;
        const auto service=services?reinterpret_cast<SharedGetter>(messageServiceGetter)(services.get()):nullptr;
        if(!service)throw std::runtime_error("native_message_service_not_ready");
        const VoiceSendOptions options{};
        // UI SendVoice constructs this native subclass with recipient, encoded
        // audio, duration and options. x0 is unused; shared_ptr result is x8.
        using Factory=std::shared_ptr<void> (*)(void *,const std::string &,const std::string &,const uint32_t *,const VoiceSendOptions &);
        const auto owned=reinterpret_cast<Factory>(api.construct)(nullptr,id,bytes,&duration,options);
        if(!owned)throw std::runtime_error("native_send_info_empty");
        auto *info=static_cast<unsigned char *>(owned.get());
        const auto &uuid=*reinterpret_cast<const std::string *>(info+0x580);
        if(uuid.empty()||uuid.size()>128)throw std::runtime_error("native_voice_layout_unverified");
        uint32_t type=34;std::memcpy(info+0xe4,&type,4);
        const bool verified=*reinterpret_cast<const std::string *>(info+0x90)==id&&*reinterpret_cast<const std::string *>(info+0x5c8)==bytes;
        if(!verified)throw std::runtime_error("native_voice_layout_unverified");
        bool queued=false;
        if(!prepare){
            const std::vector<std::shared_ptr<void>> requests{owned};
            using Send=SendResult (*)(void *,const std::vector<std::shared_ptr<void>> &);
            submitted=true;auto result=reinterpret_cast<Send>(api.send)(service.get(),requests);
            queued=result.bytes[0]==1;reinterpret_cast<void (*)(void *)>(api.destroyResult)(&result);
        }
        bridgeResult="{\"source\":\"native\",\"queued\":"+std::string(queued?"true":"false")+",\"prepared\":"+std::string(prepare?"true":"false")+",\"receiverVerified\":true,\"requestId\":";
        quote(bridgeResult,uuid);bridgeResult+="}";
    }catch(const std::runtime_error &error){bridgeResult="{\"error\":";quote(bridgeResult,submitted?"native_send_outcome_unknown":error.what());bridgeResult+='}';}
    catch(...){bridgeResult=submitted?"{\"error\":\"native_send_outcome_unknown\"}":"{\"error\":\"native_voice_prepare_failed\"}";}
    return bridgeResult.c_str();
}

#include <filesystem>
#include <algorithm>
#include <dlfcn.h>
namespace fs=std::filesystem;
static std::string nsPath(void *object){
    if(!object)return "";
    auto selector=reinterpret_cast<void *(*)(const char *)>(dlsym(RTLD_DEFAULT,"sel_registerName"));
    auto message=reinterpret_cast<const char *(*)(void *,void *)>(dlsym(RTLD_DEFAULT,"objc_msgSend"));
    const char *path=message(object,selector("UTF8String"));return path?path:"";
}
static std::vector<fs::path> mediaRoots(){
    auto home=reinterpret_cast<void *(*)()>(dlsym(RTLD_DEFAULT,"NSHomeDirectory"));
    std::vector<fs::path> roots;if(home){const auto path=nsPath(home());if(!path.empty()&&fs::path(path).is_absolute())roots.push_back(path);}
    auto cls=reinterpret_cast<void *(*)(const char *)>(dlsym(RTLD_DEFAULT,"objc_getClass"));
    auto sel=reinterpret_cast<void *(*)(const char *)>(dlsym(RTLD_DEFAULT,"sel_registerName"));
    auto msg=reinterpret_cast<void *(*)(void *,void *)>(dlsym(RTLD_DEFAULT,"objc_msgSend"));
    auto arg=reinterpret_cast<void *(*)(void *,void *,const void *)>(dlsym(RTLD_DEFAULT,"objc_msgSend"));
    if(cls&&sel&&msg&&arg){
        void *manager=msg(cls("NSFileManager"),sel("defaultManager"));
        void *group=arg(cls("NSString"),sel("stringWithUTF8String:"),"5A4RE8SF68.com.tencent.xinWeChat");
        void *url=arg(manager,sel("containerURLForSecurityApplicationGroupIdentifier:"),group);
        if(url){const auto path=nsPath(msg(url,sel("path")));if(!path.empty())roots.push_back(path);}
    }return roots;
}
static std::string base64(const std::string &data){
    static const char table[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;out.reserve((data.size()+2)/3*4);
    for(size_t n=0;n<data.size();n+=3){unsigned a=static_cast<unsigned char>(data[n]),b=n+1<data.size()?static_cast<unsigned char>(data[n+1]):0,c=n+2<data.size()?static_cast<unsigned char>(data[n+2]):0;unsigned v=(a<<16)|(b<<8)|c;out+=table[v>>18];out+=table[(v>>12)&63];out+=n+1<data.size()?table[(v>>6)&63]:'=';out+=n+2<data.size()?table[v&63]:'=';}return out;
}

static bool imageHeader(const std::string &data){
    return data.size()>=12&&(data.compare(0,3,"\xff\xd8\xff")==0||data.compare(0,4,"\x89PNG")==0||data.compare(0,3,"GIF")==0||(data.compare(0,4,"RIFF")==0&&data.compare(8,4,"WEBP")==0));
}
static std::string decodeDat(const std::string &data,const fs::path &path,const std::vector<std::string> &uins){
    if(data.size()<31||data.compare(0,6,"\x07\x08V2\x08\x07")!=0)return data;
    uint32_t aesSize,xorSize;std::memcpy(&aesSize,data.data()+6,4);std::memcpy(&xorSize,data.data()+10,4);
    const size_t aligned=(static_cast<size_t>(aesSize)/16+1)*16;
    if(aligned>data.size()-15||xorSize>data.size()-15-aligned)return data;
    std::string account;bool next=false;for(const auto &component:path){if(next){account=component.string();break;}if(component=="xwechat_files")next=true;}
    std::vector<std::string> accounts{account};const auto suffix=account.rfind('_');if(suffix!=std::string::npos){if(account.compare(0,5,"wxid_")==0&&suffix>4)accounts.push_back(account.substr(0,suffix));else if(account.size()-suffix==5)accounts.push_back(account.substr(0,suffix));}
    using Md5=unsigned char *(*)(const void *,uint32_t,unsigned char *);
    using Crypt=int (*)(unsigned,unsigned,unsigned,const void *,size_t,const void *,const void *,size_t,void *,size_t,size_t *);
    auto md5=reinterpret_cast<Md5>(dlsym(RTLD_DEFAULT,"CC_MD5"));auto crypt=reinterpret_cast<Crypt>(dlsym(RTLD_DEFAULT,"CCCrypt"));if(!md5||!crypt)return data;
    for(const auto &uin:uins)for(const auto &id:accounts){
        if(id.empty())continue;unsigned char digest[16];const auto input=uin+id;md5(input.data(),input.size(),digest);std::string key;const char digits[]="0123456789abcdef";for(unsigned char c:digest){key+=digits[c>>4];key+=digits[c&15];}key.resize(16);
        std::string plain(aligned,'\0');size_t written=0;
        if(crypt(1,0,3,key.data(),key.size(),nullptr,data.data()+15,aligned,plain.data(),plain.size(),&written)!=0)continue;plain.resize(written);
        if(written!=aesSize||!imageHeader(plain))continue;
        plain.append(data,15+aligned,data.size()-xorSize-15-aligned);
        const unsigned char xorKey=static_cast<unsigned char>(std::stoull(uin)&255);
        for(size_t n=data.size()-xorSize;n<data.size();n++)plain+=static_cast<char>(static_cast<unsigned char>(data[n])^xorKey);
        if(plain.compare(0,3,"\xff\xd8\xff")==0&&plain.compare(plain.size()-2,2,"\xff\xd9")!=0)continue;
        return plain;
    }
    return data;
}
extern "C" const char *bridge_cached_media(const char *fingerprint){
    try{
        const std::string hash(fingerprint);if(hash.size()<8||hash.size()>128||hash.find("..")!=std::string::npos||hash.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")!=std::string::npos)throw std::runtime_error("native_invalid_media_id");
        {std::lock_guard<std::mutex> guard(mediaPathMutex);const auto found=nativeMediaBlobs.find(hash);if(found!=nativeMediaBlobs.end()){bridgeResult="{\"files\":[{\"extension\":\".silk\",\"dataBase64\":\""+base64(found->second)+"\"}]}";return bridgeResult.c_str();}}
        size_t visited=0,accessible=0,totalBytes=0;bool tooLarge=false;std::vector<fs::path> candidates;
        {std::lock_guard<std::mutex> guard(mediaPathMutex);const auto found=nativeMediaPaths.find(hash);if(found!=nativeMediaPaths.end())candidates.push_back(found->second);}std::vector<std::string> uins;
        for(const auto &root:candidates.empty()?mediaRoots():std::vector<fs::path>{}){
            std::error_code error;fs::recursive_directory_iterator it(root,fs::directory_options::skip_permission_denied,error),end;
            if(error)continue;accessible++;
            for(;it!=end&&visited<150000;it.increment(error)){
                if(error){error.clear();continue;}visited++;
                if(it->is_symlink(error)){it.disable_recursion_pending();continue;}if(!it->is_regular_file(error))continue;
                const auto path=it->path();const auto name=path.filename().string();
                if(name.compare(0,4,"key_")==0&&path.extension()==".statistic"){
                    const auto stop=name.find('_',4);const auto code=name.substr(4,stop-4);
                    if(code.size()>0&&code.size()<=10&&code.find_first_not_of("0123456789")==std::string::npos&&uins.size()<32)uins.push_back(code);
                }
                auto candidate=path.string(),needle=hash;if(hash.find_first_not_of("0123456789abcdefABCDEF")==std::string::npos){for(char &c:candidate)if(c>='A'&&c<='Z')c+=32;for(char &c:needle)if(c>='A'&&c<='Z')c+=32;}
                if(candidate.find(needle)!=std::string::npos&&candidates.size()<12)candidates.push_back(path);
            }
        }
        // Prefer originals to thumbnails, then permit a thumbnail fallback.
        std::sort(candidates.begin(),candidates.end(),[](const fs::path &a,const fs::path &b){return a.filename().string().find("_t.dat")==std::string::npos&&b.filename().string().find("_t.dat")!=std::string::npos;});
        std::string json="{\"files\":[";bool first=true;
        for(const auto &path:candidates){std::error_code error;const auto size=fs::file_size(path,error);if(error||size==0)continue;if(size>128*1024*1024){tooLarge=true;continue;}if(totalBytes+size>128*1024*1024)continue;
            std::ifstream input(path,std::ios::binary);std::string data(size,'\0');if(!input.read(data.data(),size))continue;data=decodeDat(data,path,uins);totalBytes+=size;
            if(!first)json+=',';first=false;json+="{\"extension\":";quote(json,path.extension().string());json+=",\"isThumbnail\":"+std::string(path.filename().string().find("_t.dat")!=std::string::npos?"true":"false");json+=",\"dataBase64\":\""+base64(data)+"\"}";
        }
        json+="],\"tooLarge\":"+std::string(tooLarge?"true":"false")+",\"visited\":"+std::to_string(visited)+",\"accessibleRoots\":"+std::to_string(accessible)+"}";bridgeResult=std::move(json);
    }catch(...){bridgeResult="{\"error\":\"native_media_read_failed\"}";}return bridgeResult.c_str();
}

extern "C" const char *bridge_capture_recorded_voice(const void *context,const void *audio){
    try{
        const auto *value=static_cast<const unsigned char *>(audio);
        uint32_t first,second;std::memcpy(&first,value,4);std::memcpy(&second,value+4,4);
        const uint64_t duration=static_cast<uint64_t>(first)*second;
        const auto &data=*reinterpret_cast<const std::string *>(value+8);
        const auto &chat=*reinterpret_cast<const std::string *>(static_cast<const unsigned char *>(context)+0x1c0);
        if(duration<1000||duration>60000||data.empty()||data.size()>1048576)throw std::runtime_error("native_recording_invalid");
        bridgeResult="{\"durationMs\":"+std::to_string(duration)+",\"chatId\":";quote(bridgeResult,chat,1024);bridgeResult+=",\"dataBase64\":";quote(bridgeResult,base64(data),1398104);bridgeResult+='}';
    }catch(...){bridgeResult="{\"error\":\"native_recording_invalid\"}";}return bridgeResult.c_str();
}

#include <dispatch/dispatch.h>
struct ImportControlWork {void *window;void *stop;void *cancel;void *busy;uint32_t action;uint32_t kind=0;const char *error=nullptr;};
static void importControlMain(void *context){
    auto &work=*static_cast<ImportControlWork *>(context);
    try{
        work.kind=reinterpret_cast<uint32_t (*)()>(work.busy)();
        if(work.action==1){
            if(work.kind!=0){work.error="native_migration_busy";return;}
            reinterpret_cast<void (*)(uint32_t)>(work.window)(1);
        }else if(work.action==2){
            if(work.kind!=0&&work.kind!=4){work.error="native_migration_busy";return;}
            reinterpret_cast<void (*)()>(work.cancel)();
            reinterpret_cast<void (*)()>(work.stop)();
            // The window factory activates an existing window when passed 0;
            // closing is performed through its exact Accessibility close control.
        }
        work.kind=reinterpret_cast<uint32_t (*)()>(work.busy)();
    }catch(...){work.error="native_migration_failed";}
}
extern "C" const char *bridge_import_control(void *window,void *stop,void *cancel,void *busy,uint32_t action){
    ImportControlWork work{window,stop,cancel,busy,action};
    dispatch_sync_f(dispatch_get_main_queue(),&work,importControlMain);
    if(work.error){bridgeResult="{\"error\":";quote(bridgeResult,work.error);bridgeResult+='}';}
    else bridgeResult="{\"source\":\"native\",\"busyKind\":"+std::to_string(work.kind)+"}";
    return bridgeResult.c_str();
}

extern "C" const char *bridge_self_id(void *accountGetter,void *manager){
    try{
        const auto account=reinterpret_cast<SharedGetter>(accountGetter)(manager);
        if(!account)throw std::runtime_error("native_account_not_ready");
        auto **vtable=*reinterpret_cast<void ***>(manager);
        const auto &id=reinterpret_cast<const std::string &(*)(void *)>(vtable[0x28/8])(manager);
        if(id.empty())throw std::runtime_error("native_account_not_ready");
        bridgeResult="{\"id\":";quote(bridgeResult,id,1024);bridgeResult+='}';
    }catch(...){bridgeResult="{\"error\":\"native_account_not_ready\"}";}
    return bridgeResult.c_str();
}

// Attachment lookup accepts native record metadata, never a client path.
extern "C" const char *bridge_cached_attachment(const char *fileName,uint64_t expectedSize,const char *accountId){
    try{
        const std::string name(fileName),account(accountId);
        if(name.empty()||name.size()>512||name=="."||name==".."||name.find_first_of("/\\")!=std::string::npos||account.empty()||!expectedSize||expectedSize>128*1024*1024)throw std::runtime_error("native_invalid_media_id");
        std::vector<fs::path> candidates;size_t visited=0,nameMatches=0,sizeMatches=0,ownedMatches=0;std::set<std::string> seen;
        for(const auto &root:mediaRoots()){
            std::error_code error;fs::recursive_directory_iterator it(root,fs::directory_options::skip_permission_denied,error),end;
            for(;it!=end&&visited<500000;it.increment(error)){
                if(error){error.clear();continue;}visited++;
                if(it->is_symlink(error)){it.disable_recursion_pending();continue;}
                if(it->is_directory(error)){
                    const auto directory=it->path().filename().string();
                    if(directory==".git"||directory=="node_modules"||directory==".codex"||directory==".tools")it.disable_recursion_pending();
                    continue;
                }
                if(!it->is_regular_file(error))continue;
                const bool named=it->path().filename().string()==name,sized=fs::file_size(it->path(),error)==expectedSize;
                if(named)nameMatches++;if(sized)sizeMatches++;
                if(!named||!sized)continue;
                bool next=false,owned=false,inCache=false;
                for(const auto &part:it->path()){
                    const auto component=part.string();
                    if(next){owned=component==account||component.compare(0,account.size()+1,account+"_")==0;next=false;}
                    if(component=="xwechat_files"){next=true;inCache=true;}
                }
                if(!inCache||!owned)continue;ownedMatches++;
                const auto path=fs::canonical(it->path(),error);if(!error&&seen.insert(path.string()).second)candidates.push_back(path);
            }
        }
        if(candidates.size()>1){bridgeResult="{\"error\":\"native_attachment_ambiguous\"}";return bridgeResult.c_str();}
        std::string json="{\"files\":[";
        if(candidates.size()==1){std::ifstream input(candidates.front(),std::ios::binary);std::string data(expectedSize,'\0');if(!input.read(data.data(),expectedSize))throw std::runtime_error("native_media_read_failed");json+="{\"dataBase64\":\""+base64(data)+"\"}";}
        bridgeResult=json+"],\"cacheLookup\":{\"nameMatches\":"+std::to_string(nameMatches)+",\"sizeMatches\":"+std::to_string(sizeMatches)+",\"ownedMatches\":"+std::to_string(ownedMatches)+",\"visited\":"+std::to_string(visited)+"}}";
    }catch(const std::runtime_error &error){bridgeResult="{\"error\":";quote(bridgeResult,error.what());bridgeResult+='}';}
    catch(...){bridgeResult="{\"error\":\"native_media_read_failed\"}";}return bridgeResult.c_str();
}
