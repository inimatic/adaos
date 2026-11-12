#include "AdaVoiceMetrics.h"
#include <math.h>
AdaVoiceMetrics AdaVoiceMeasure(const int16_t *pcm, size_t count) {
    AdaVoiceMetrics result = {0};
    if (!pcm || !count || count > 192000) return result;
    double sum=0, squares=0, best=0;
    for (size_t i=0; i<count; i++) {
        double x=pcm[i]; sum+=x; squares+=x*x;
        if (pcm[i]>=32760 || pcm[i]<=-32760) result.clipped++;
    }
    result.rms=sqrt(fmax(0,squares/count-(sum/count)*(sum/count)));
    const unsigned frequencies[]={500,1000,2000};
    for (size_t start=0; start+320<=count; start+=320) {
        double frameSum=0, energy=0;
        for (size_t i=0; i<320; i++) { double x=pcm[start+i]; frameSum+=x; energy+=x*x; }
        energy-=frameSum*frameSum/320;
        if (energy<320) continue;
        for (unsigned f=0; f<3; f++) {
            double coefficient=2*cos(6.283185307179586*frequencies[f]/16000), q1=0,q2=0;
            for (size_t i=0; i<320; i++) {
                double q=pcm[start+i]+coefficient*q1-q2; q2=q1; q1=q;
            }
            double ratio=2*(q1*q1+q2*q2-coefficient*q1*q2)/(320*energy);
            if (ratio>best) { best=ratio; result.tone_hz=frequencies[f]; }
        }
    }
    result.tone_ppm=(unsigned)(fmin(1,fmax(0,best))*1000000);
    return result;
}
