/* SPDX-License-Identifier: GPL-2.0-only
 * Read-only state from the last constitutive/force evaluation, before output
 * changes stress measures. Commit binds it to an accepted displacement frame.
 * Includes oxygen_contact_telemetry.h and oxygen_native_tangent.h first.
 */
#ifndef OXYGEN_ACCEPTED_BULK_H
#define OXYGEN_ACCEPTED_BULK_H
static ITG oxygen_bulk_step=-1, oxygen_bulk_increment=-1, oxygen_bulk_nodes=0;
static ITG oxygen_bulk_iteration=0;
static ITG oxygen_bulk_labels=0, oxygen_bulk_states=0, oxygen_bulk_points=0;
static double oxygen_bulk_time=0.;
static unsigned long long oxygen_bulk_serial=0, oxygen_bulk_bytes=0;
static const char *oxygen_bulk_unavailable=NULL;

static int oxygen_bulk_enabled(void) {
    const char *value=getenv("O3D_CCX_ACCEPTED_BULK");
    return value && !strcmp(value,"1");
}

static void oxygen_accepted_bulk_stage(const char *jobname, ITG step,
    ITG increment, ITG iteration, double time, double duration, ITG nodes,
    ITG labels, const ITG *mi, const ITG *ipkon, const ITG *kon,
    const char *lakon, const ITG *ielmat, const ITG *ielorien, ITG orientations,
    const ITG *nelcon, const ITG *iperturb, ITG state_count, ITG mpcs,
    ITG thermal, ITG command, ITG output, const double *displacement,
    const double *previous_displacement, const double *force,
    const double *tangent, const double *stress,
    const double *initial_stress, const double *initial_strain,
    const double *initial_state, const double *state) {
    FILE *stream; char filename[256];
    ITG e,q,active=0,points=0,nq,nn,material,kind,field;
    const double *values;
    uint16_t byte_order=1;
    unsigned long long size;
    if (!oxygen_bulk_enabled()) return;
    oxygen_bulk_step=step;oxygen_bulk_increment=increment;
    oxygen_bulk_nodes=nodes;oxygen_bulk_time=time;
    oxygen_bulk_iteration=iteration;oxygen_bulk_unavailable=NULL;
    oxygen_bulk_labels=labels;oxygen_bulk_states=state_count;
    ++oxygen_bulk_serial;
    if (command!=0 || output!=0 || thermal || mpcs ||
        mi[1]!=3 || state_count<0 || state_count>64) {
        oxygen_bulk_unavailable="unsupported_bulk_evaluation_context";return;
    }
    if (nodes<1 || nodes>100000 || labels<1 || labels>150000) {
        oxygen_bulk_unavailable="accepted_bulk_label_budget";return;
    }
    for(e=0;e<labels;++e) {
        if(ipkon[e]<0) continue;
        if(!memcmp(lakon+8*e,"C3D10   ",8)){nq=4;nn=10;}
        else if(!memcmp(lakon+8*e,"C3D4    ",8)){nq=1;nn=4;}
        else {oxygen_bulk_unavailable="unsupported_bulk_element_family";return;}
        material=ielmat[mi[2]*e];
        if(material<1 || (orientations && (!ielorien || ielorien[mi[2]*e]!=0)) || mi[0]<nq) {
            oxygen_bulk_unavailable="unsupported_bulk_material_mapping";return;
        }
        kind=nelcon[2*(material-1)];
        if(kind!=2 && kind!=-51) {
            oxygen_bulk_unavailable="unsupported_bulk_material_law";return;
        }
        ++active;points+=nq;
    }
    if(active<1 || active>10000 || points>40000) {
        oxygen_bulk_unavailable="accepted_bulk_quadrature_budget";return;
    }
    oxygen_bulk_points=points;
    size=3ULL*(mi[1]+1)*nodes*sizeof(double)+
        (unsigned long long)active*14*sizeof(ITG)+
        (unsigned long long)points*(39+3*state_count)*sizeof(double)+
        (unsigned long long)nodes*3*sizeof(double)+2048ULL;
    if(size>67108864ULL || oxygen_bulk_bytes+size>536870912ULL) {
        oxygen_bulk_unavailable="accepted_bulk_output_budget";return;
    }
    oxygen_tangent_name(filename,sizeof(filename),jobname,".accepted-bulk.pending");
    stream=fopen(filename,"wb");
    if(!stream) oxygen_contact_output_error("cannot create accepted bulk staging file");
    fprintf(stream,"{\"schema\":1,\"layout\":\"constitutive-force-state-v2\","
        "\"step\":%" ITGFORMAT ",\"increment\":%" ITGFORMAT ",\"iteration\":%" ITGFORMAT
        ",\"nodes\":%" ITGFORMAT ",\"stride\":%" ITGFORMAT ",\"elements\":%" ITGFORMAT
        ",\"points\":%" ITGFORMAT ",\"state_count\":%" ITGFORMAT ",\"iperturb\":[%" ITGFORMAT ",%" ITGFORMAT
        "],\"integer_bytes\":%zu,\"real_bytes\":%zu,\"byte_order\":\"%s\",\"evaluation_time\":",
        step,increment,iteration,nodes,mi[1]+1,active,points,state_count,
        iperturb[0],iperturb[1],sizeof(ITG),sizeof(double),
        *(unsigned char *)&byte_order?"little":"big");
    oxygen_contact_real(stream,time);fputs(",\"increment_duration\":",stream);
    oxygen_contact_real(stream,duration);fputs("}\n",stream);
    oxygen_tangent_write(stream,displacement,sizeof(double),(size_t)(mi[1]+1)*nodes);
    oxygen_tangent_write(stream,previous_displacement,sizeof(double),(size_t)(mi[1]+1)*nodes);
    oxygen_tangent_write(stream,force,sizeof(double),(size_t)(mi[1]+1)*nodes);
    /* Records: label, node count, quadrature count, native material kind,
       then ten node labels, padded with zero for linear tetrahedra. */
    for(e=0;e<labels;++e) {
        ITG record[14]={0};
        if(ipkon[e]<0) continue;
        nn=!memcmp(lakon+8*e,"C3D10",5)?10:4;nq=nn==10?4:1;
        record[0]=e+1;record[1]=nn;record[2]=nq;
        record[3]=nelcon[2*(ielmat[mi[2]*e]-1)];
        for(q=0;q<nn;++q) record[4+q]=kon[ipkon[e]+q];
        oxygen_tangent_write(stream,record,sizeof(ITG),14);
    }
    /* Field-major point arrays. Stress is PK2 for NLGEOM, Cauchy for small
       strain. The 21 tangent entries use native tensor/shear ordering. */
    for(field=0;field<6;++field) for(e=0;e<labels;++e) {
        ITG width;
        if(ipkon[e]<0) continue;
        nq=!memcmp(lakon+8*e,"C3D10",5)?4:1;
        width=field==0?21:(field<4?6:state_count);
        if(!width) continue;
        for(q=0;q<nq;++q) {
            if(field==0) values=tangent+27*(mi[0]*e+q);
            else if(field==1) values=stress+6*(mi[0]*e+q);
            else if(field==2) values=initial_stress+6*(mi[0]*e+q);
            else if(field==3) values=initial_strain+6*(mi[0]*e+q);
            else if(field==4) values=initial_state+state_count*(mi[0]*e+q);
            else values=state+state_count*(mi[0]*e+q);
            if(width) oxygen_tangent_write(stream,values,sizeof(double),width);
        }
    }
    if(fclose(stream)) oxygen_contact_output_error("cannot close accepted bulk staging file");
}

static void oxygen_accepted_bulk_commit(const char *jobname, ITG step,
    ITG increment,double time,ITG nodes,const ITG *mi,const double *accepted,
    ITG labels,const ITG *ipkon,const char *lakon,ITG state_count,
    const double *accepted_state) {
    static FILE *manifest=NULL;static unsigned long long frames=0;
    FILE *source=NULL,*target;char filename[256],pending[256],suffix[80],buffer[65536];
    long bytes=0;size_t count;ITG node,e,q,nq,points=0;
    if(!oxygen_bulk_enabled()) return;
    if(!manifest) {
        oxygen_tangent_name(filename,sizeof(filename),jobname,".accepted-bulk.jsonl");
        manifest=fopen(filename,"wb");
        if(!manifest) oxygen_contact_output_error("cannot create accepted bulk manifest");
        fputs("{\"type\":\"header\",\"schema\":1,\"upstream\":\"2.23\","
            "\"instrumentation\":\"accepted-constitutive-force-state-v1\","
            "\"source_sha256\":\"9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7\"}\n",manifest);
    }
    ++frames;
    if(!oxygen_bulk_serial || step!=oxygen_bulk_step || increment!=oxygen_bulk_increment || nodes!=oxygen_bulk_nodes ||
       labels!=oxygen_bulk_labels || state_count!=oxygen_bulk_states ||
       fabs(time-oxygen_bulk_time)>1e-10*fmax(fabs(time),1.))
        oxygen_bulk_unavailable="no_matching_bulk_evaluation";
    if(!oxygen_bulk_unavailable) {
        for(e=0;e<labels;++e) if(ipkon[e]>=0) points+=!memcmp(lakon+8*e,"C3D10",5)?4:1;
        if(points!=oxygen_bulk_points) oxygen_bulk_unavailable="bulk_layout_changed_before_output";
    }
    if(!oxygen_bulk_unavailable) {
        oxygen_tangent_name(pending,sizeof(pending),jobname,".accepted-bulk.pending");
        source=fopen(pending,"rb");
        if(!source || fseek(source,0,SEEK_END)) oxygen_contact_output_error("missing accepted bulk staging file");
        bytes=ftell(source);if(bytes<0) oxygen_contact_output_error("accepted bulk size unavailable");
        bytes+=(long)((size_t)nodes*3*sizeof(double));rewind(source);
        bytes+=(long)((size_t)points*state_count*sizeof(double));
        if(oxygen_bulk_bytes+(unsigned long long)bytes>536870912ULL) oxygen_bulk_unavailable="accepted_bulk_output_budget";
    }
    fprintf(manifest,"{\"type\":\"frame\",\"step\":%" ITGFORMAT ",\"increment\":%" ITGFORMAT
        ",\"iteration\":%" ITGFORMAT ",\"evaluation_serial\":%llu,\"accepted_time\":",
        step,increment,oxygen_bulk_iteration,oxygen_bulk_serial);oxygen_contact_real(manifest,time);
    if(oxygen_bulk_unavailable) {
        fprintf(manifest,",\"available\":false,\"reason\":\"%s\"}\n",oxygen_bulk_unavailable);
        if(source) fclose(source);
    } else {
        snprintf(suffix,sizeof(suffix),".accepted-bulk-%08llu.bin",frames);
        oxygen_tangent_name(filename,sizeof(filename),jobname,suffix);target=fopen(filename,"wb");
        if(!target) oxygen_contact_output_error("cannot create accepted bulk frame");
        while((count=fread(buffer,1,sizeof(buffer),source))) oxygen_tangent_write(target,buffer,1,count);
        if(ferror(source)) oxygen_contact_output_error("cannot copy accepted bulk frame");
        fclose(source);
        for(node=0;node<nodes;++node) oxygen_tangent_write(target,accepted+(mi[1]+1)*node+1,sizeof(double),3);
        if(state_count) for(e=0;e<labels;++e) {
            if(ipkon[e]<0) continue;
            nq=!memcmp(lakon+8*e,"C3D10",5)?4:1;
            for(q=0;q<nq;++q) oxygen_tangent_write(target,
                accepted_state+state_count*(mi[0]*e+q),sizeof(double),state_count);
        }
        if(fclose(target)) oxygen_contact_output_error("cannot close accepted bulk frame");
        oxygen_bulk_bytes+=(unsigned long long)bytes;
        fprintf(manifest,",\"available\":true,\"file\":\"%s\",\"bytes\":%ld}\n",filename,bytes);
    }
    if(fflush(manifest)) oxygen_contact_output_error("cannot flush accepted bulk manifest");
}
#endif
