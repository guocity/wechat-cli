// Signed scheduling code for the validated WeChat 4.1.15 AppEx framework.
// Uses its non-nestable task queue; no runtime machine-code patching or callbacks.
#include <dlfcn.h>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>

namespace {
std::atomic<int> state{0};
std::string output;
struct Job { void* isolate; std::string source; };
using Open=int(*)(void*,void**);
using Close=int(*)(void*,void*);
using String=int(*)(void*,const char*,size_t,void**);
using Eval=int(*)(void*,void*,void**);
using Read=int(*)(void*,void*,char*,size_t,size_t*);

void run(Job* job) {
    auto eval=reinterpret_cast<Eval>(dlsym(RTLD_DEFAULT,"napi_run_script"));
    Dl_info image{};
    if(!eval || !dladdr(reinterpret_cast<void*>(eval),&image)) {
        output="native_api_unavailable";state.store(-1);return;
    }
    auto base=static_cast<char*>(image.dli_fbase);
    // These offsets and their code signatures are checked by channels-agent.js.
    auto descriptor=base+0xb61c228;
    using Tls=void*(*)(void*);
    auto tls=*reinterpret_cast<Tls*>(descriptor);
    if(*static_cast<void**>(tls(descriptor))!=job->isolate) {
        output="native_worker_changed";state.store(-1);return;
    }
    alignas(16) unsigned char env[512]{};
    auto vt=static_cast<char*>(dlsym(RTLD_DEFAULT,"_ZTV10napi_env__"));
    auto open=reinterpret_cast<Open>(dlsym(RTLD_DEFAULT,"napi_open_handle_scope"));
    auto close=reinterpret_cast<Close>(dlsym(RTLD_DEFAULT,"napi_close_handle_scope"));
    auto str=reinterpret_cast<String>(dlsym(RTLD_DEFAULT,"napi_create_string_utf8"));
    auto read=reinterpret_cast<Read>(dlsym(RTLD_DEFAULT,"napi_get_value_string_utf8"));
    if(!vt||!open||!close||!str||!read){output="native_api_unavailable";state.store(-1);return;}
    // Reuse the GC-tracked context root itself, not a copied tagged pointer.
    void* context=static_cast<char*>(job->isolate)+63840;
    *reinterpret_cast<void**>(env)=vt+16;
    *reinterpret_cast<void**>(env+8)=job->isolate;
    *reinterpret_cast<void**>(env+16)=context;
    *reinterpret_cast<int*>(env+0x98)=1;
    *reinterpret_cast<int*>(env+0xa8)=8;
    auto enter=reinterpret_cast<void(*)(void*)>(base+0xf74e4);
    auto leave=reinterpret_cast<void(*)(void*)>(base+0x61b788);
    void* scope=nullptr;void* script=nullptr;void* result=nullptr;
    int rc=open(env,&scope);
    if(!rc) {
        enter(context);
        rc=str(env,job->source.data(),job->source.size(),&script);
        if(!rc)rc=eval(env,script,&result);
        if(!rc) {
            size_t count=0;rc=read(env,result,nullptr,0,&count);
            if(!rc&&count<=1024*1024) {
                std::string buffer(count+1,'\0');
                rc=read(env,result,buffer.data(),buffer.size(),&count);
                if(!rc)output.assign(buffer.data(),count);
            } else if(!rc)rc=1;
        }
        if(rc) {
            // Dispose any caught exception handle before the temporary env goes away.
            using Clear=int(*)(void*,void**);
            auto clear=reinterpret_cast<Clear>(dlsym(RTLD_DEFAULT,"napi_get_and_clear_last_exception"));
            void* exception=nullptr;if(clear)clear(env,&exception);
        }
        leave(context);close(env,scope);
    }
    if(rc)output="native_script_failed";
    state.store(rc ? -1 : 1);
}

class SignedTask {
public:
    explicit SignedTask(Job* value): job(value) {}
    virtual ~SignedTask() { if(job){delete job;output="native_worker_closed";state.store(-1);} }
    virtual void Run() { Job* value=job;job=nullptr;run(value);delete value; }
private:
    Job* job;
};
}
extern "C" int channels_evaluate_worker(void* runner,void* isolate,const char* source) {
    if(!runner||!isolate||!source||strlen(source)>131072)return -1;
    int expected=state.load();
    do {if(expected==2)return -1;}while(!state.compare_exchange_weak(expected,2));
    auto table=*static_cast<void***>(runner);
    // Validated libc++ trivial-ABI unique_ptr passes Task* directly; receiver owns it.
    using Post=void(*)(void*,SignedTask*,const void*);
    const void* location=nullptr;
    reinterpret_cast<Post>(table[6])(runner,new SignedTask(new Job{isolate,source}),&location);
    return 0;
}
extern "C" int channels_state(){return state.load();}
extern "C" const char* channels_result(){return state.load()==2 ? "pending" : output.c_str();}
